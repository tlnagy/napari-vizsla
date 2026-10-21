import logging
import warnings
from importlib import resources
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
    PushButton,
    create_widget,
)
from napari.qt import thread_worker
from qtpy.QtCore import QTimer
from qtpy.QtWidgets import QSizePolicy
from skimage import measure
from skimage.io import imread

from .utils import get_successor_tracklets

PIXEL_DRAG_THRESHOLD = 3

if TYPE_CHECKING:
    import napari


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

        self._autosave_dir_selector = FileEdit(
            label='Autosave Folder',
            mode='d',
            value=None,
            tooltip='Autosave to this directory. Overwrites contents.',
        )
        self._autosave_dir_selector.changed.connect(self.update_autosave_dir)

        self._graph = None
        self._selected_df = None

        self._shape_layer = viewer.add_shapes(
            name='Vizsla',
            properties={
                'tracklet_id': np.array([]),
                'verts': np.array([], dtype=str),
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

        logopath = resources.files('napari_vizsla.assets').joinpath(
            'vizsla.png'
        )
        with resources.as_file(logopath) as path:
            logo = Image(value=imread(path))
        logo.native.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum)
        logo.native.setMaximumHeight(100)
        logo.native.setMaximumWidth(300)
        button_box = Container(layout='horizontal', labels=False, label='')
        link_button = PushButton(
            text='Link (l)', tooltip='Link selected tracks'
        )
        break_button = PushButton(
            text='Break (b)', tooltip='Break selected track'
        )
        link_button.clicked.connect(self.link)
        break_button.clicked.connect(self.break_track)
        button_box.extend([link_button, break_button])
        top_box = Container(layout='vertical')
        top_box.extend(
            [
                self._seg_layer_combo,
                self._tracking_layer_combo,
                self._load_ctc_dir,
                self._autosave_dir_selector,
                button_box,
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

    @thread_worker
    def graph_loader(self, ctcdir, create_track_layer=True):
        graph = td.graph.RustWorkXGraph()

        def contour(regionprop, tolerance=2):
            c = measure.find_contours(np.pad(regionprop.image, 1))[0] - 1
            return measure.approximate_polygon(c, tolerance).tolist()

        is_selected = lambda region: False
        is_selected.__name__ = 'is_selected'
        td.io.from_ctc(
            ctcdir,
            graph,
            region_props_kwargs={'extra_properties': [contour, is_selected]},
        )

        if create_track_layer:
            # convert to napari format in background thread
            tracks_coords, tracks_graph = td.functional.to_napari_format(
                graph, solution_key=None, allow_frame_skip=True
            )

            return graph, tracks_coords, tracks_graph
        else:
            return graph

    def load_graph(self, ctcdir):
        if ctcdir is None:
            return
        self._auto_save_progbar.visible = True
        self._auto_save_progbar.min = 0
        self._auto_save_progbar.max = 0
        self._auto_save_progbar.value = 0
        self._auto_save_progbar.label = 'Loading...'
        self.native.layout().invalidate()
        create_track_layer = self._tracking_layer_combo.value is None
        self._load_graph_worker = self.graph_loader(
            ctcdir, create_track_layer=create_track_layer
        )

        def on_finished(res):
            self._auto_save_progbar.max = 100
            self._auto_save_progbar.value = 100
            self._auto_save_progbar.visible = False
            self.native.layout().invalidate()

            if create_track_layer:
                graph, tracks_coords, tracks_graph = res
                self._graph = graph
                trks = self._viewer.add_tracks(
                    data=tracks_coords,
                    name='tracks',
                    graph=tracks_graph,
                )
                self._tracking_layer_combo.value = trks
                self._viewer.layers.append(self._viewer.layers.pop('Vizsla'))
                trks.refresh()
            else:
                self._graph = res

            # set up tracks auto-save mechanism
            self.autosave_dir = Path(ctcdir).parent / '.vizsla_autosave'
            self._autosave_dir_selector.value = self.autosave_dir
            self.autosave_dir.mkdir(parents=True, exist_ok=True)
            self.current_save_worker = None
            self.save_pending = False
            self.debounce_timer = QTimer()
            self.debounce_timer.setSingleShot(True)
            self.debounce_timer.timeout.connect(self._trigger_save)

            self._tracking_layer_combo.value.events.data.connect(
                self.on_tracks_modified
            )

        self._load_graph_worker.returned.connect(on_finished)
        self._load_graph_worker.start()

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

        if self._graph is None:
            warnings.warn(
                'No graph loaded. Please load a CTC directory first.',
                stacklevel=2,
            )
            return

        label = seg_layer.get_value(event.position)

        # clear Vizsla layer if background is clicked
        if label is None or label == 0:
            self._reset_shape_layer()
            return

        tidx = int(event.position[-3])
        node = self._graph.filter(
            (td.NodeAttr('label') == label) & (td.NodeAttr('t') == tidx)
        )
        trkid = node.node_attrs('tracklet_id').item()
        nodeid = node.node_attrs('node_id').item()
        nodes = self._graph.filter(td.NodeAttr('tracklet_id') == trkid)
        nodes_df = nodes.node_attrs()

        self._graph.update_node_attrs(
            node_ids=nodes.node_ids(), attrs={'is_selected': True}
        )
        self._selected_df = self._graph.filter(
            td.NodeAttr('is_selected') == True
        ).node_attrs()

        curr_node = nodes_df.filter(pl.col('t') == tidx)
        ymin, xmin, ymax, xmax = curr_node['bbox'].item()

        polygon_pts = np.vstack(np.array(curr_node['contour'][0]))

        shape_layer.add_polygons(
            [
                polygon_pts + np.array([ymin, xmin])
            ],  # add offset to polygon points
            edge_width=10,
            edge_color='red',
            face_color='transparent',
            z_index=1,
        )
        shape_layer.features.loc[
            shape_layer.features.index[-1], 'tracklet_id'
        ] = trkid
        shape_layer.features.loc[shape_layer.features.index[-1], 'verts'] = ''

        trk_nodes = self._selected_df.filter(pl.col('tracklet_id') == trkid)
        prev_verts = trk_nodes.filter(pl.col('t') <= tidx)[:, ['y', 'x']]
        next_verts = trk_nodes.filter(pl.col('t') >= tidx)[:, ['y', 'x']]

        if prev_verts.height > 1:
            shape_layer.add_paths(
                prev_verts.to_numpy(), edge_color='white', edge_width=4
            )
        else:
            shape_layer.add_paths(
                np.array([[0, 0], [0, 0]]), edge_color='white', edge_width=4
            )
        shape_layer.features.loc[
            shape_layer.features.index[-1], 'tracklet_id'
        ] = trkid
        shape_layer.features.loc[shape_layer.features.index[-1], 'verts'] = (
            'prev'
        )

        if next_verts.height > 1:
            shape_layer.add_paths(
                next_verts.to_numpy()[::-1, :], edge_color='gray', edge_width=4
            )
        else:
            shape_layer.add_paths(
                np.array([[0, 0], [0, 0]]), edge_color='gray', edge_width=4
            )
        shape_layer.features.loc[
            shape_layer.features.index[-1], 'tracklet_id'
        ] = trkid
        shape_layer.features.loc[shape_layer.features.index[-1], 'verts'] = (
            'next'
        )

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

    def on_time_change(self):
        shape_layer = self._shape_layer

        # if no segments are selected, no updates are needed
        if self._selected_df is None:
            return

        if len(self._viewer.dims.current_step) < 3:
            warnings.warn(
                'Vizsla requires a 3D or higher dimension image to function.',
                stacklevel=2,
            )
            return
        tidx = self._viewer.dims.current_step[-3]

        data = shape_layer.data.copy()
        df = shape_layer.features.copy()
        df['shape_type'] = shape_layer.shape_type
        df['shape_index'] = range(shape_layer.nshapes)
        df['edge_color'] = np.unstack(shape_layer.edge_color)
        df['face_color'] = np.unstack(shape_layer.face_color)
        df['edge_width'] = shape_layer.edge_width

        for i in range(shape_layer.nshapes):
            if df.loc[i, 'shape_type'] == 'polygon':
                trkid = shape_layer.features.loc[i, 'tracklet_id']
                trk_nodes = self._selected_df.filter(
                    pl.col('tracklet_id') == trkid
                )
                if not trk_nodes.is_empty():
                    # update the polygon vertices to relevant what is stored in the graph for this time point
                    curr_node = trk_nodes.filter(pl.col('t') == tidx)

                    # if the current time point doesn't exist for this track, we don't update
                    if curr_node.is_empty():
                        c = df.loc[i, 'edge_color']
                        df.loc[i, 'edge_color'][:] = np.array(
                            [0.5, c[1], c[2], c[3]]
                        )
                        continue
                    ymin, xmin, ymax, xmax = curr_node['bbox'].item()
                    polygon_pts = np.vstack(np.array(curr_node['contour'][0]))
                    data[i] = polygon_pts + np.array([ymin, xmin])

                    c = df.loc[i, 'edge_color']
                    df.loc[i, 'edge_color'][:] = np.array(
                        [1.0, c[1], c[2], c[3]]
                    )

            elif df.loc[i, 'shape_type'] == 'path':
                trkid = shape_layer.features.loc[i, 'tracklet_id']

                trk_nodes = self._selected_df.filter(
                    pl.col('tracklet_id') == trkid
                )
                if df.loc[i, 'verts'] == 'prev':
                    prev_verts = trk_nodes.filter(pl.col('t') <= tidx)[
                        :, ['y', 'x']
                    ]
                    if prev_verts.height > 1:
                        data[i] = prev_verts.to_numpy()
                    else:
                        data[i] = np.array([[0, 0], [0, 0]])
                elif df.loc[i, 'verts'] == 'next':
                    next_verts = trk_nodes.filter(pl.col('t') >= tidx)[
                        :, ['y', 'x']
                    ]
                    if next_verts.height > 1:
                        data[i] = next_verts.to_numpy()[::-1, :]
                    else:
                        data[i] = np.array([[0, 0], [0, 0]])

        shape_layer.data = data
        shape_layer.features = df
        try:
            shape_layer.shape_type = df['shape_type'].to_list()
            shape_layer.edge_color = np.vstack(df['edge_color'])
            shape_layer.face_color = np.vstack(df['face_color'])
            shape_layer.edge_width = df['edge_width'].to_list()
        except ValueError:
            pass
        shape_layer.refresh()

        shape_layer.editable = False
        shape_layer.refresh()

    def hide_seg_layer(self, viewer):
        if self._seg_layer_combo.value is not None:
            self._seg_layer_combo.value.visible = (
                not self._seg_layer_combo.value.visible
            )

    def hide_track_layer(self, viewer):
        if self._tracking_layer_combo.value is not None:
            self._tracking_layer_combo.value.visible = (
                not self._tracking_layer_combo.value.visible
            )

    def hide_polys(self, viewer):
        shape_layer = self._shape_layer
        colors = shape_layer.edge_color.copy()
        for i in range(shape_layer.nshapes):
            if shape_layer.shape_type[i] == 'polygon':
                c = colors[i]
                if c[3] == 1.0:
                    c[3] = 0.0
                else:
                    c[3] = 1.0
                colors[i][:] = c
            elif shape_layer.shape_type[i] == 'path':
                c = colors[i]
                if c[3] == 1.0:
                    c[3] = 0.3
                else:
                    c[3] = 1.0
                colors[i][:] = c
        shape_layer.edge_color = colors
        shape_layer.refresh()

    def _reset_shape_layer(self):
        self._shape_layer.data = []
        self._shape_layer.properties = {
            'tracklet_id': np.array([]),
            'verts': np.array([], dtype=str),
        }
        self._shape_layer.editable = False

        # reset the selection flag for all nodes in the graph
        self._graph.update_node_attrs(
            node_ids=self._graph.node_ids(),
            attrs={'is_selected': False},
        )
        self._selected_df = None

    def break_track(self, viewer):
        tidx = self._viewer.dims.current_step[-3]
        if self._selected_df['tracklet_id'].n_unique() > 1:
            warnings.warn('Select only one label to break track', stacklevel=2)
            return
        src = self._selected_df.filter(pl.col('t') == tidx)['node_id'].item()
        for succ in self._graph.successors(src):
            self._graph.remove_edge(src, succ)

        tracks_coords, tracks_graph = td.functional.to_napari_format(
            self._graph, solution_key=None, allow_frame_skip=True
        )
        self._tracking_layer_combo.value.data = tracks_coords
        self._tracking_layer_combo.value.refresh()

        self._reset_shape_layer()

    def link(self, viewer):
        df = self._selected_df
        if df['t'].n_unique() < df.height:
            warnings.warn(
                'Cannot link tracklets with overlapping timepoints!',
                stacklevel=2,
            )
            return
        if df['tracklet_id'].n_unique() > 2:
            warnings.warn('>3 way links are not supported!', stacklevel=2)
            return

        idx = df.with_row_index().filter(pl.col('tracklet_id').diff() != 0)[
            'index'
        ][0]

        if len(self._graph.successors(df[idx - 1, 'node_id'])) > 0:
            warnings.warn(
                'Cannot link tracklets, as the first tracklet has children already!',
                stacklevel=2,
            )
            return

        if len(self._graph.predecessors(df[idx, 'node_id'])) > 0:
            warnings.warn(
                'Cannot link tracklets, as the second tracklet has a parent already!',
                stacklevel=2,
            )
            return

        self._graph.add_edge(df[idx - 1, 'node_id'], df[idx, 'node_id'], {})

        tracks_coords, tracks_graph = td.functional.to_napari_format(
            self._graph, solution_key=None, allow_frame_skip=True
        )
        self._tracking_layer_combo.value.data = tracks_coords
        self._tracking_layer_combo.value.refresh()

        self._reset_shape_layer()

    def update_autosave_dir(self):
        self.autosave_dir = self._autosave_dir_selector.value

    def on_tracks_modified(self):
        """Triggered every time the user edits the tracks layer."""
        self.debounce_timer.start(self.get_autosave_freq() * 1000)

    def _trigger_save(self):
        """Trigger the save operation after the debounce time has passed."""
        if self.current_save_worker is not None:
            self.save_pending = True
            return

        try:
            self._auto_save_progbar.visible = True
        except RuntimeError as e:
            if 'wrapped C/C++ object' in str(e):
                return  # Vizsla was already closed/deleted, exit cleanly
            raise
        self._auto_save_progbar.min = 0
        self._auto_save_progbar.max = 0
        self._auto_save_progbar.value = 0
        self._auto_save_progbar.label = 'Saving...'
        self.native.layout().invalidate()
        graph_snapshot = self._graph.copy()

        def on_save_complete():
            self.current_save_worker = None

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
        self.current_save_worker = self.background_saver(graph_snapshot)
        self.current_save_worker.finished.connect(on_save_complete)
        self.current_save_worker.start()

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

    def get_autosave_freq(self) -> float:
        try:
            from napari.settings import get_plugin_settings

            return get_plugin_settings('napari-vizsla').autosave.autosave_freq
        except ImportError:
            return 300
