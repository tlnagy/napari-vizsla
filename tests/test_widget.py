import pytest
import tracksdata as td

from napari_vizsla._widget import Vizsla


class FakeEvent:
    def __init__(self, position, event_type='mouse_move'):
        self.position = position
        self.type = event_type


@pytest.fixture
def load_vizsla(make_napari_viewer):
    viewer = make_napari_viewer()
    v = Vizsla(viewer)
    viewer.window.add_dock_widget(v)
    viewer.open('tests/example_tracks', stack=True)

    return v, viewer


@pytest.fixture
def load_vizsla_w_graph(load_vizsla):
    v, viewer = load_vizsla
    v._load_ctc_dir.value = 'tests/example_tracks/'
    return v, viewer


def test_vizsla_layer_on_top(load_vizsla):
    v, viewer = load_vizsla

    # Check if Vizsla layer is still on top
    assert viewer.layers[-1].name == 'Vizsla'


def test_vizsla_graph_update(load_vizsla_w_graph):
    v, viewer = load_vizsla_w_graph

    assert v._graph.num_nodes() == 4335


def simulate_click(viewer, v, position):
    viewer.dims.current_step = (position[-3], 0, 0)
    event = FakeEvent(position=position)
    gen = v.display_tracks(v._shape_layer, event)

    next(gen)
    next(gen)
    event.type = 'mouse_release'

    with pytest.raises(StopIteration):
        next(gen)


def test_vizsla_mouse_click_highlight(load_vizsla_w_graph):
    v, viewer = load_vizsla_w_graph

    simulate_click(viewer, v, (7, 1111, 2648))

    assert 'polygon' in v._shape_layer.shape_type


def test_vizsla_link(load_vizsla_w_graph):
    v, viewer = load_vizsla_w_graph

    # click on first label
    simulate_click(viewer, v, (20, 761, 2759))

    # click on second label
    simulate_click(viewer, v, (21, 726, 2627))

    # link labels
    v.link()

    assert (
        len(v._graph.filter(td.NodeAttr('tracklet_id') == 97).node_ids()) == 30
    )

    # make sure everything is cleared
    assert 'polygon' not in v._shape_layer.shape_type


def test_vizsla_break(load_vizsla_w_graph):
    v, viewer = load_vizsla_w_graph

    simulate_click(viewer, v, (7, 1111, 2648))

    v.break_track()

    assert (
        len(v._graph.filter(td.NodeAttr('tracklet_id') == 97).node_ids()) == 8
    )
