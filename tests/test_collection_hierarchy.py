"""Pure tests for the collection-hierarchy core (no bpy).

Exercises scene_introspect.build_collection_groups (tree -> Group norms + reparent),
the mixed childIndex assignment, and prop_mapper's guid_key namespacing.

Run:  python tests/test_collection_hierarchy.py  (or pytest)
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(HERE, "..", "minervha_material_exporter")
sys.path.insert(0, PKG)

import prop_mapper        # noqa: E402
import scene_introspect   # noqa: E402

KEY = scene_introspect._collection_guid_key


def _obj(name, parent=None):
    return {"name": name, "kind": "mesh", "mesh_key": name + "Mesh", "parent_name": parent,
            "child_index": 0, "visible": True, "transform": scene_introspect._identity_transform(),
            "material_slots": [],
            "validation": {"has_uv": True, "procedural_materials": [], "risky_transform": None}}


# Master(objects=[A]) -> Props(objects=[B]) -> Inner(objects=[C]); plus an empty sibling.
def _nested_tree():
    return {"name": "Master", "objects": ["A"], "children": [
        {"name": "Props", "objects": ["B"], "children": [
            {"name": "Inner", "objects": ["C"], "children": []}]},
        {"name": "Empty", "objects": [], "children": []}]}


def _groups_by_name(group_norms):
    return {g["name"]: g for g in group_norms}


def test_scene_scope_root_not_emitted_and_prunes_empty():
    norms = [_obj("A"), _obj("B"), _obj("C")]
    groups, reparent = scene_introspect.build_collection_groups(norms, _nested_tree(), emit_root_as_group=False)
    by = _groups_by_name(groups)
    assert set(by) == {"Props", "Inner"}, by          # Master implicit root, Empty pruned
    assert by["Props"]["parent_name"] is None          # top-level group sits at root
    assert by["Inner"]["parent_name"] == KEY("Props")
    assert reparent == {"A": None, "B": KEY("Props"), "C": KEY("Inner")}


def test_collection_scope_root_emitted():
    norms = [_obj("A"), _obj("B"), _obj("C")]
    groups, reparent = scene_introspect.build_collection_groups(norms, _nested_tree(), emit_root_as_group=True)
    by = _groups_by_name(groups)
    assert set(by) == {"Master", "Props", "Inner"}, by
    assert by["Master"]["parent_name"] is None
    assert by["Props"]["parent_name"] == KEY("Master")
    assert by["Inner"]["parent_name"] == KEY("Props")
    assert reparent == {"A": KEY("Master"), "B": KEY("Props"), "C": KEY("Inner")}


def test_groups_are_identity_kind_group():
    groups, _ = scene_introspect.build_collection_groups([_obj("B")], _nested_tree(), emit_root_as_group=False)
    g = _groups_by_name(groups)["Props"]
    assert g["kind"] == "group" and g["mesh_key"] is None
    assert g["transform"] == scene_introspect._identity_transform()
    assert g["guid_key"] == KEY("Props")


def test_object_with_inscope_parent_not_rehomed_and_prunes_its_collection():
    # D is parented to E (in scope) -> D keeps that parent; the collection holding only D is pruned.
    tree = {"name": "Master", "objects": ["E"], "children": [
        {"name": "Coll", "objects": ["D"], "children": []}]}
    norms = [_obj("E"), _obj("D", parent="E")]
    groups, reparent = scene_introspect.build_collection_groups(norms, tree, emit_root_as_group=False)
    assert _groups_by_name(groups) == {} or "Coll" not in _groups_by_name(groups)
    assert "D" not in reparent                          # stays under its object-parent E
    assert reparent.get("E") is None


def test_multiple_membership_first_in_dfs_wins():
    # X is linked into both Props and its child Inner; DFS pre-order visits Props first.
    tree = {"name": "Master", "objects": [], "children": [
        {"name": "Props", "objects": ["X"], "children": [
            {"name": "Inner", "objects": ["X"], "children": []}]}]}
    groups, reparent = scene_introspect.build_collection_groups([_obj("X")], tree, emit_root_as_group=False)
    assert reparent == {"X": KEY("Props")}
    assert set(_groups_by_name(groups)) == {"Props"}    # Inner claims nothing -> pruned


def test_out_of_scope_object_in_tree_is_ignored():
    # The tree may list objects that aren't in scope (e.g. excluded elsewhere) -> never claimed.
    groups, reparent = scene_introspect.build_collection_groups([_obj("B")], _nested_tree(), emit_root_as_group=False)
    assert "A" not in reparent and "C" not in reparent  # only B is in scope
    assert set(_groups_by_name(groups)) == {"Props"}    # Inner(C) and Empty pruned


def test_mixed_child_index_deterministic():
    norms = [_obj("A"), _obj("B"), _obj("C")]
    groups, reparent = scene_introspect.build_collection_groups(norms, _nested_tree(), emit_root_as_group=True)
    for n in norms:
        if n["name"] in reparent:
            n["parent_name"] = reparent[n["name"]]
    combined = norms + groups
    scene_introspect._assign_child_indices(combined)
    idx = {n["name"]: n["child_index"] for n in combined}
    # under Master: object "A" (0) before group "Props" (1)
    assert idx["A"] == 0 and idx["Props"] == 1
    # under Props: object "B" (0) before group "Inner" (1)
    assert idx["B"] == 0 and idx["Inner"] == 1
    assert idx["C"] == 0 and idx["Master"] == 0


# ── prop_mapper guid_key ────────────────────────────────────────────────────

def test_guid_key_namespaces_collection_vs_object():
    ck = KEY("Chair")
    coll = {"name": "Chair", "kind": "group", "guid_key": ck, "parent_name": None,
            "transform": scene_introspect._identity_transform(), "material_slots": []}
    child = _obj("ChairSeat", parent=ck)
    gp = prop_mapper.map_object(coll)
    cp = prop_mapper.map_object(child, mesh_path="X/Models/ChairSeatMesh.obj")
    # a collection and an object of the same name never collide; the child points at the group.
    assert gp["guid"] == prop_mapper.make_guid(ck)
    assert gp["guid"] != prop_mapper.make_guid("Chair")
    assert cp["parent"] == gp["guid"]
    assert gp["label"] == "Chair"                       # label stays the real name


def test_object_without_guid_key_unchanged():
    plain = prop_mapper.map_object(_obj("Chair"))
    assert plain["guid"] == prop_mapper.make_guid("Chair")


def run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    fails = []
    for t in tests:
        try:
            t()
        except AssertionError as e:
            fails.append((t.__name__, str(e)))
    if fails:
        print(f"COLLECTION HIERARCHY FAILED — {len(fails)} failure(s):")
        for name, msg in fails:
            print(f"  {name}: {msg}")
        sys.exit(1)
    print(f"COLLECTION HIERARCHY OK — {len(tests)} tests passed")


if __name__ == "__main__":
    run()
