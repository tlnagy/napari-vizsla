def get_successor_tracklets(graph, node_id):
    """
    Returns immediate child tracklets of the given node_id
    """
    descendants = set()
    children = graph.successors(
        node_id, attr_keys=['node_id', 'tracklet_id'], return_attrs=True
    )

    for child in children.rows(named=True):
        # If this is a branch point, add each child and stop recursing
        if child['tracklet_id'] != graph.nodes[node_id]['tracklet_id']:
            descendants.add(child['tracklet_id'])
        else:
            # Continue recursing with the child
            descendants.update(
                get_successor_tracklets(graph, child['node_id'])
            )

    return descendants
