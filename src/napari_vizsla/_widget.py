import logging
import warnings
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import polars as pl
import tracksdata as td
from magicgui.widgets import (
    Container,
    FileEdit,
    Image,
    ProgressBar,
    create_widget,
)
from napari.qt import thread_worker
from qtpy.QtCore import QObject, QRunnable, QTimer, Signal
from qtpy.QtWidgets import QSizePolicy
from skimage import measure
from skimage.io import imread

from .utils import get_successor_tracklets

PIXEL_DRAG_THRESHOLD = 3
DEBOUNCE_TIME_MS = 2000  # 2 seconds

if TYPE_CHECKING:
    import napari


class WorkerSignals(QObject):
    finished = Signal(dict)


class LoadGraphWorker(QRunnable):
    def __init__(self, ctcdir, graph):
        super().__init__()
        self.ctcdir = ctcdir
        self._graph = graph
        self.signals = WorkerSignals()  # Attach the signals object

    def run(self):
        # load graph
        td.io.from_ctc(self.ctcdir, self._graph)

        # convert to napari format in background thread
        tracks_coords, tracks_graph = td.functional.to_napari_format(
            self._graph, solution_key=None, allow_frame_skip=True
        )
        self.signals.finished.emit(
            {'coords': tracks_coords, 'graph': tracks_graph}
        )


class Vizsla(Container):
    def __init__(self, viewer: 'napari.viewer.Viewer'):
        super().__init__(layout='vertical')
        self._viewer = viewer

        logging.getLogger('tracksdata.utils._logging').setLevel(logging.ERROR)

        default_seg_layer = next(
            (
                layer
                for layer in viewer.layers
                if type(layer).__name__ == 'Labels'
            ),
            None,
        )
        self._seg_layer_combo = create_widget(
            label='Segmentation',
            annotation='napari.layers.Labels',
            value=default_seg_layer,
        )
        default_tracking_layer = next(
            (
                layer
                for layer in viewer.layers
                if type(layer).__name__ == 'Tracks'
            ),
            None,
        )
        self._tracking_layer_combo = create_widget(
            label='Tracking',
            annotation='napari.layers.Tracks',
            value=default_tracking_layer,
        )

        self._load_ctc_dir = FileEdit(
            label='CTC folder',
            mode='d',
            value=None,
            tooltip='Load a CTC folder to visualize and edit tracking data',
        )
        self._load_ctc_dir.changed.connect(self.load_graph)

        self._graph = td.graph.RustWorkXGraph()
        self._polys = defaultdict(dict)

        self._shape_layer = viewer.add_shapes(
            name='Vizsla',
            properties={
                'label': np.array([]),
                'timepoint': np.array([]),
                'tracklet_id': np.array([]),
            },
        )
        self._shape_layer.opacity = 1.0
        self._shape_layer.editable = False

        self._shape_layer.bind_key('s', self.hide_seg_layer, overwrite=True)
        self._shape_layer.bind_key('b', self.break_track, overwrite=True)
        self._shape_layer.bind_key('t', self.hide_track_layer, overwrite=True)
        self._shape_layer.bind_key('l', self.link, overwrite=True)
        self._shape_layer.bind_key('h', self.hide_polys, overwrite=True)

        self._shape_layer.mouse_drag_callbacks.append(self.display_tracks)
        self._viewer.dims.events.current_step.connect(self.on_time_change)

        self._viewer.layers.events.inserted.connect(self.on_layer_inserted)
        self._viewer.layers.events.moved.connect(self.on_layer_moved)

        self._auto_save_progbar = ProgressBar(
            value=0, max=0, visible=False, label='Saving...'
        )

        logo = Image(value=imread('assets/vizsla.png'))
        # logo.native.setScaledContents(True)
        logo.native.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum)
        logo.native.setMaximumHeight(100)
        logo.native.setMaximumWidth(300)
        top_box = Container(layout='vertical')
        top_box.extend(
            [
                self._seg_layer_combo,
                self._tracking_layer_combo,
                self._load_ctc_dir,
            ]
        )
        self.labels = False
        spacer = Container(layout='vertical')
        spacer.native.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Expanding
        )
        bottom_box = Container(layout='vertical')
        # self.native.setStyleSheet('border: 1px solid red;')
        bottom_box.native.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Fixed
        )
        bottom_box.extend([self._auto_save_progbar])

        self.extend([logo, top_box, spacer, bottom_box])
        # self.extend([logo])

        self.native.layout().setStretch(0, 0)  # Top doesn't stretch
        self.native.layout().setStretch(1, 0)
        self.native.layout().setStretch(
            2, 1
        )  # Spacer gets all stretch priority
        self.native.layout().setStretch(3, 0)  # Bottom doesn't stretch

        self.native.setMaximumWidth(self.native.minimumSizeHint().width())

    def load_graph(self, ctcdir):
        if ctcdir is None:
            return
        self._auto_save_progbar.visible = True
        self._auto_save_progbar.min = 0
        self._auto_save_progbar.max = 0
        self._auto_save_progbar.value = 0
        self._auto_save_progbar.label = 'Loading...'
        self.native.layout().invalidate()
        worker = LoadGraphWorker(ctcdir, self._graph)

        def on_finished(loaded_data):
            self._auto_save_progbar.max = 100
            self._auto_save_progbar.value = 100
            self._auto_save_progbar.visible = False
            self.native.layout().invalidate()

            if self._tracking_layer_combo.value is None:
                trks = self._viewer.add_tracks(
                    data=loaded_data['coords'],
                    name='tracks',
                    graph=loaded_data['graph'],
                )
                self._tracking_layer_combo.value = trks
                self._viewer.layers.append(self._viewer.layers.pop('Vizsla'))
                trks.refresh()

                # set up tracks auto-save mechanism
                self.autosave_dir = Path(ctcdir).parent / '.vizsla_autosave'
                self.autosave_dir.mkdir(parents=True, exist_ok=True)
                self.current_save_worker = None
                self.save_pending = False
                self.debounce_timer = QTimer()
                self.debounce_timer.setSingleShot(True)
                self.debounce_timer.timeout.connect(self._trigger_save)

                trks.events.data.connect(self.on_tracks_modified)

        worker.signals.finished.connect(on_finished)
        worker.run()

    def display_tracks(self, shape_layer, event):
        """
        Display the track/child tracks for the clicked segmentation label.
        """

        start_pos = event.position

        yield
        is_dragged = False

        while event.type == 'mouse_move':
            current_pos = np.array(event.position)
            distance = np.linalg.norm(current_pos - start_pos)

            if distance > PIXEL_DRAG_THRESHOLD:
                is_dragged = True

            yield

        if is_dragged:
            return

        seg_layer = self._seg_layer_combo.value

        if self._graph.num_nodes() == 0:
            warnings.warn(
                'No graph loaded. Please load a CTC directory first.',
                stacklevel=2,
            )
            return

        label = seg_layer.get_value(event.position)

        # clear Vizsla layer if background is clicked
        if label is None or label == 0:
            shape_layer.data = []
            shape_layer.properties = {
                'label': np.array([]),
                'timepoint': np.array([]),
                'tracklet_id': np.array([]),
            }
            shape_layer.editable = False
            return

        tidx = int(event.position[-3])
        node = self._graph.filter(
            (td.NodeAttr('label') == label) & (td.NodeAttr('t') == tidx)
        )
        trkid = node.node_attrs('tracklet_id').item()
        nodeid = node.node_attrs('node_id').item()
        nodes = self._graph.filter(
            td.NodeAttr('tracklet_id') == trkid
        ).node_attrs()

        for n in nodes.rows(named=True):
            ymin, xmin, ymax, xmax = n['bbox']

            # remove all labels in the bbox that don't belong to this nucleus
            region = np.array(
                np.pad(
                    seg_layer.data[n['t'], ymin:ymax, xmin:xmax],
                    pad_width=((1, 1), (2, 2)),
                    mode='constant',
                    constant_values=0,
                )
            )
            region[region != n['label']] = 0

            self._polys[trkid][n['t']] = measure.find_contours(region)[0] + (
                ymin - 1,
                xmin - 1,
            )

        shape_layer.add_polygons(
            self._polys[trkid][tidx],
            edge_width=10,
            edge_color='red',
            face_color='transparent',
            z_index=1,
        )
        shape_layer.features.loc[shape_layer.features.index[-1], 'label'] = (
            label
        )
        shape_layer.features.loc[
            shape_layer.features.index[-1], 'timepoint'
        ] = tidx
        shape_layer.features.loc[
            shape_layer.features.index[-1], 'tracklet_id'
        ] = trkid

        prev_verts = nodes.filter(pl.col('t') <= tidx)[:, ['y', 'x']]
        next_verts = nodes.filter(pl.col('t') >= tidx)[:, ['y', 'x']]
        tmod = tidx  # this is needed because we might have to transfer one timepoint from prev to next or vice versa

        # if this track has more than 3 vertices than we can split them up into prev and future vertices
        # so that we can have nicely highlighted tracks.
        if prev_verts.shape[0] + next_verts.shape[0] >= 3:
            if prev_verts.shape[0] < 2:
                prev_verts = pl.concat((prev_verts, next_verts[1, :]))
                next_verts = next_verts[1:, :]
                tmod += 1
            shape_layer.add_paths(
                prev_verts.to_numpy(), edge_color='white', edge_width=4
            )
            shape_layer.features.loc[
                shape_layer.features.index[-1], 'label'
            ] = label
            shape_layer.features.loc[
                shape_layer.features.index[-1], 'timepoint'
            ] = tmod
            shape_layer.features.loc[
                shape_layer.features.index[-1], 'tracklet_id'
            ] = trkid

            if next_verts.shape[0] < 2:
                next_verts = pl.concat((prev_verts[-2, :], next_verts))
                prev_verts = prev_verts[:-1, :]
                tmod -= 1
            shape_layer.add_paths(
                next_verts.to_numpy()[::-1, :], edge_color='gray', edge_width=4
            )
            shape_layer.features.loc[
                shape_layer.features.index[-1], 'label'
            ] = label
            shape_layer.features.loc[
                shape_layer.features.index[-1], 'timepoint'
            ] = tmod - 1
            shape_layer.features.loc[
                shape_layer.features.index[-1], 'tracklet_id'
            ] = trkid

        for track in get_successor_tracklets(self._graph, nodeid):
            sucverts = self._graph.filter(
                td.NodeAttr('tracklet_id') == track
            ).node_attrs(['y', 'x'])
            if sucverts.shape[0] > 1:
                shape_layer.add_paths(
                    sucverts.to_numpy(), edge_color='#8B4000', edge_width=4
                )
            circle = np.vstack(
                (next_verts.to_numpy()[-1, :], np.array([10.0, 10]))
            )
            shape_layer.add_ellipses(
                circle, face_color='orange', edge_color='transparent'
            )
            shape_layer.add_paths(
                np.vstack(
                    (next_verts.to_numpy()[-1, :], sucverts.to_numpy()[0, :])
                ),
                edge_color='orange',
                edge_width=4,
            )

    def on_time_change(self, event):
        shape_layer = self._shape_layer
        tidx = self._viewer.dims.current_step[-3]
        seg_layer = self._seg_layer_combo.value

        shape_data = shape_layer.data.copy()

        for i in range(shape_layer.nshapes):
            if shape_layer.shape_type[i] == 'polygon':
                trkid = shape_layer.features.loc[i, 'tracklet_id']
                if trkid in self._polys and tidx in self._polys[trkid]:
                    shape_data[i] = self._polys[trkid][tidx]
                    # update the polygon label with actual label info from the segmentation image
                    label = seg_layer.get_value(
                        [tidx] + np.mean(shape_data[i], axis=0).tolist()
                    )
                    shape_layer.features.loc[i, 'timepoint'] = tidx
                    shape_layer.features.loc[i, 'label'] = label
                # c = df.loc[i, "edge_color"]
                c = shape_layer.edge_color[i]
                shape_layer.selected_data = {i}
                if shape_layer.features.loc[i, 'timepoint'] == tidx:
                    c[0] = 1.0
                else:
                    c[0] = 0.5
                shape_layer.current_edge_color = c
                shape_layer.selected_data = {}

        df = shape_layer.features.copy()
        df['shape_type'] = shape_layer.shape_type
        df['shape_index'] = range(shape_layer.nshapes)
        df['timepoint'] = tidx - df['timepoint']
        tracklet_groups = df[df['shape_type'] == 'path'].groupby('tracklet_id')

        for trkid, group_df in tracklet_groups:
            group_df.sort_values(by='timepoint', inplace=True)
            if group_df.shape[0] != 2:
                break
            to_transfer = int(group_df['timepoint'].iloc[0])
            if to_transfer == 0.0:
                break  # nothing to do
            elif to_transfer < 0.0:
                src, dst = group_df['shape_index']
            else:
                dst, src = group_df['shape_index']
            if shape_data[src].shape[0] - abs(to_transfer) < 2:
                break
            # vertices to transfer from src to dst only if this time point exists for this track
            if trkid in self._polys and tidx in self._polys[trkid]:
                transfer = shape_data[src][
                    -2 : (-1 * (abs(to_transfer) + 2)) : -1
                ]
                shape_data[dst] = np.vstack((shape_data[dst], transfer))
                shape_data[src] = shape_data[src][: -1 * abs(to_transfer)]
            shape_layer.features.loc[dst, 'timepoint'] += to_transfer
            shape_layer.features.loc[src, 'timepoint'] += to_transfer

        shape_layer.data = shape_data
        shape_layer.editable = False
        shape_layer.refresh()

    def hide_seg_layer(self, v):
        if self._seg_layer_combo.value is not None:
            self._seg_layer_combo.value.visible = (
                not self._seg_layer_combo.value.visible
            )

    def hide_track_layer(self, v):
        if self._tracking_layer_combo.value is not None:
            self._tracking_layer_combo.value.visible = (
                not self._tracking_layer_combo.value.visible
            )

    def hide_polys(self, v):
        shape_layer = self._shape_layer
        for i in range(shape_layer.nshapes):
            if shape_layer.shape_type[i] == 'polygon':
                shape_layer.selected_data = {i}
                c = shape_layer.edge_color[i].copy()
                if c[3] == 1.0:
                    c[3] = 0.0
                else:
                    c[3] = 1.0
                shape_layer.current_edge_color = c
                shape_layer.selected_data = {}
            elif shape_layer.shape_type[i] == 'path':
                shape_layer.selected_data = {i}
                c = shape_layer.edge_color[i].copy()
                if c[3] == 1.0:
                    c[3] = 0.3
                else:
                    c[3] = 1.0
                shape_layer.current_edge_color = c
                shape_layer.selected_data = {}

    def break_track(self, v):
        shape_layer = self._shape_layer
        selected = [
            i for (i, p) in enumerate(shape_layer.shape_type) if p == 'polygon'
        ]
        if len(selected) != 1:
            warnings.warn('Select only one label to break track', stacklevel=2)
            return
        timepoints = shape_layer.features['timepoint'][selected].to_numpy()[0]
        labels = shape_layer.features['label'][selected].to_numpy()[0]
        src = self._graph.filter(
            (td.NodeAttr('t') == timepoints) & (td.NodeAttr('label') == labels)
        ).node_ids()[0]
        for succ in self._graph.successors(src):
            self._graph.remove_edge(src, succ)

        tracks_coords, tracks_graph = td.functional.to_napari_format(
            self._graph, solution_key=None, allow_frame_skip=True
        )
        self._tracking_layer_combo.value.data = tracks_coords
        self._tracking_layer_combo.value.refresh()

        shape_layer.data = []
        shape_layer.properties = {
            'label': np.array([]),
            'timepoint': np.array([]),
        }
        shape_layer.editable = False

    def link(self, v):
        shape_layer = self._shape_layer
        selected = [
            i for (i, p) in enumerate(shape_layer.shape_type) if p == 'polygon'
        ]
        timepoints = shape_layer.features['timepoint'][selected].to_numpy()
        labels = shape_layer.features['label'][selected].to_numpy()

        if len(selected) > 3:
            warnings.warn('4 way links are not supported!', stacklevel=2)
            return
        if len(np.unique(timepoints)) < 2:
            warnings.warn('Cannot link within same timepoint!', stacklevel=2)
            return
        values, counts = np.unique_counts(timepoints)
        if counts[np.argmin(values)] != 1:
            warnings.warn('Cannot merge tracklets!', stacklevel=2)
            return

        for child in np.where(timepoints != np.min(timepoints))[0]:
            src = self._graph.filter(
                (td.NodeAttr('t') == np.min(timepoints))
                & (td.NodeAttr('label') == labels[np.argmin(timepoints)])
            ).node_ids()[0]
            dst = self._graph.filter(
                (td.NodeAttr('t') == timepoints[child])
                & (td.NodeAttr('label') == labels[child])
            ).node_ids()[0]

            for pred in self._graph.predecessors(dst):
                self._graph.remove_edge(pred, dst)

            self._graph.add_edge(src, dst, {})

            tracks_coords, tracks_graph = td.functional.to_napari_format(
                self._graph, solution_key=None, allow_frame_skip=True
            )
            self._tracking_layer_combo.value.data = tracks_coords
            self._tracking_layer_combo.value.refresh()

            shape_layer.data = []
            shape_layer.properties = {
                'label': np.array([]),
                'timepoint': np.array([]),
            }
            shape_layer.editable = False

    def on_tracks_modified(self, event):
        """Triggered every time the user edits the tracks layer."""
        self.debounce_timer.start(DEBOUNCE_TIME_MS)

    def _trigger_save(self):
        """Trigger the save operation after the debounce time has passed."""
        if self.current_save_worker is not None:
            self.save_pending = True
            return

        self._auto_save_progbar.visible = True
        self._auto_save_progbar.min = 0
        self._auto_save_progbar.max = 0
        self._auto_save_progbar.value = 0
        self._auto_save_progbar.label = 'Saving...'
        self.native.layout().invalidate()
        graph_snapshot = self._graph.copy()

        def on_save_complete():
            self.current_worker = None

            self._auto_save_progbar.max = 100
            self._auto_save_progbar.value = 100
            self._auto_save_progbar.label = 'Save complete'
            self._auto_save_progbar.visible = False
            self.native.layout().invalidate()
            # If edits happened while we were saving, trigger a new save with the latest data
            if self.save_pending:
                self.save_pending = False
                self._trigger_save()

        # Start the background worker
        self.current_worker = self.background_saver(graph_snapshot)
        self.current_worker.finished.connect(on_save_complete)
        self.current_worker.start()

    @thread_worker
    def background_saver(self, graph_snapshot):
        """Runs in the background thread."""
        graph_snapshot.to_ctc(self.autosave_dir, overwrite=True)

    def on_layer_inserted(self, event):
        """
        Update the segmentation or tracking layer selection when layers are inserted
        """
        if self._seg_layer_combo.value is None and isinstance(
            event.value, self._seg_layer_combo.annotation
        ):
            print('Updating segmentation layer selection')
            self._seg_layer_combo.choices = list(self._viewer.layers)
            self._seg_layer_combo.value = event.value

        elif self._tracking_layer_combo.value is None and isinstance(
            event.value, self._tracking_layer_combo.annotation
        ):
            self._tracking_layer_combo.choices = list(self._viewer.layers)
            self._tracking_layer_combo.value = event.value

        # make sure to keep the Vizsla layer on top of the stack and selected
        if event.value.name != 'Vizsla':
            self._viewer.layers.append(self._viewer.layers.pop('Vizsla'))
            QTimer.singleShot(
                0,
                lambda: setattr(
                    self._viewer.layers.selection,
                    'active',
                    self._viewer.layers['Vizsla'],
                ),
            )

    def on_layer_moved(self, event):
        """
        Ensure the Vizsla layer remains on top of the stack when layers are moved.
        """
        if event.value.name != 'Vizsla':
            self._viewer.layers.append(self._viewer.layers.pop('Vizsla'))
            QTimer.singleShot(
                0,
                lambda: setattr(
                    self._viewer.layers.selection,
                    'active',
                    self._viewer.layers['Vizsla'],
                ),
            )
