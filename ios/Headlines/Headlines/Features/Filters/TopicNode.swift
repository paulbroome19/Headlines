//
//  TopicNode.swift
//  Headlines
//
//  The recursive model behind the briefing filter tree, plus the view-model
//  that owns selection/expansion state. Reused verbatim by onboarding's
//  BuildBriefingView and the in-app Filters screen — the only thing that
//  changes between them is the seed data (see TopicSeed.swift).
//

import SwiftUI

// MARK: - Node

/// One node in the filter tree. Leaves (no children) carry the real on/off
/// selection; parents *derive* their state from their descendants.
struct TopicNode: Identifiable {
    let id: String
    let label: String
    var children: [TopicNode]
    /// Meaningful for leaves only; for parents the state is computed from
    /// descendants (see `FilterTreeModel.selection(of:)`).
    var isOn: Bool
    var isExpanded: Bool

    init(id: String,
         label: String,
         children: [TopicNode] = [],
         isOn: Bool = false,
         isExpanded: Bool = false) {
        self.id = id
        self.label = label
        self.children = children
        self.isOn = isOn
        self.isExpanded = isExpanded
    }

    var isLeaf: Bool { children.isEmpty }
}

/// Tri-state selection for a row's toggle.
enum NodeSelection { case on, off, partial }

// MARK: - Tree view-model

/// Owns the tree and all mutation/derivation. The view observes this single
/// object, so there's no nested-ObservableObject churn — every edit rebuilds
/// the value-type tree and republishes once.
final class FilterTreeModel: ObservableObject {
    @Published private(set) var roots: [TopicNode]

    init(roots: [TopicNode]) {
        self.roots = roots
    }

    /// Replace the whole tree — e.g. after fetching live categories from the
    /// backend. Selection/expansion start fresh from the new nodes.
    func replaceRoots(_ newRoots: [TopicNode]) {
        roots = newRoots
    }

    /// Rehydrate a seed tree from a previously-persisted set of selected leaf ids.
    convenience init(seed: [TopicNode], selectedLeafIDs: Set<String>) {
        func apply(_ node: TopicNode) -> TopicNode {
            var n = node
            if n.isLeaf {
                n.isOn = selectedLeafIDs.contains(n.id)
            } else {
                n.children = n.children.map(apply)
            }
            return n
        }
        self.init(roots: seed.map(apply))
    }

    // MARK: Derived selection state

    /// A node is `on` if every descendant leaf is on, `off` if every descendant
    /// leaf is off, and `partial` otherwise. Leaves report their own `isOn`.
    func selection(of node: TopicNode) -> NodeSelection {
        if node.isLeaf { return node.isOn ? .on : .off }
        let kids = node.children.map(selection(of:))
        if kids.allSatisfy({ $0 == .on })  { return .on }
        if kids.allSatisfy({ $0 == .off }) { return .off }
        return .partial
    }

    // MARK: Mutations (recurse by id, rebuild the tree)

    /// Cascade: turning a parent ON lights every descendant; OFF clears them.
    /// A node that is currently `partial` (or `off`) flips to fully ON.
    func toggleSelect(_ id: String) {
        roots = roots.map { walkSelect($0, target: id) }
    }

    private func walkSelect(_ node: TopicNode, target: String) -> TopicNode {
        var n = node
        if n.id == target {
            let turnOn = selection(of: n) != .on   // off/partial -> on, on -> off
            return setAll(n, on: turnOn)
        }
        n.children = n.children.map { walkSelect($0, target: target) }
        return n
    }

    private func setAll(_ node: TopicNode, on: Bool) -> TopicNode {
        var n = node
        if n.isLeaf {
            n.isOn = on
        } else {
            n.children = n.children.map { setAll($0, on: on) }
        }
        return n
    }

    func toggleExpand(_ id: String) {
        roots = roots.map { walkExpand($0, target: id) }
    }

    private func walkExpand(_ node: TopicNode, target: String) -> TopicNode {
        var n = node
        if n.id == target {
            n.isExpanded.toggle()
            return n
        }
        n.children = n.children.map { walkExpand($0, target: target) }
        return n
    }

    // MARK: Flatten to the currently-visible rows

    struct Row: Identifiable {
        let id: String
        let label: String
        let depth: Int
        let hasChildren: Bool
        let isExpanded: Bool
        let selection: NodeSelection
    }

    /// Depth-first list of the rows that should currently render — children of a
    /// collapsed parent are omitted.
    var visibleRows: [Row] {
        var out: [Row] = []
        func walk(_ nodes: [TopicNode], depth: Int) {
            for n in nodes {
                out.append(Row(id: n.id,
                               label: n.label,
                               depth: depth,
                               hasChildren: !n.isLeaf,
                               isExpanded: n.isExpanded,
                               selection: selection(of: n)))
                if !n.isLeaf && n.isExpanded { walk(n.children, depth: depth + 1) }
            }
        }
        walk(roots, depth: 0)
        return out
    }

    // MARK: Persistence

    /// The flat set of selected leaf ids — the minimal state needed to restore
    /// the tree. Encode this to JSON for `@AppStorage`.
    func selectedLeafIDs() -> [String] {
        var out: [String] = []
        func walk(_ nodes: [TopicNode]) {
            for n in nodes {
                if n.isLeaf {
                    if n.isOn { out.append(n.id) }
                } else {
                    walk(n.children)
                }
            }
        }
        walk(roots)
        return out
    }
}

// MARK: - Backend categories → tree

extension TopicNode {
    /// Build a filter tree from the live backend taxonomy (`GET /data/categories`).
    /// Each group becomes a parent; its subcategories become selectable leaves.
    /// A group with no subcategories is itself a selectable leaf. The node ids ARE
    /// the real backend slugs, so `selectedLeafIDs()` yields valid filter slugs.
    static func tree(from groups: [CategoryGroup]) -> [TopicNode] {
        groups.map { group in
            group.subcategories.isEmpty
                ? TopicNode(id: group.slug, label: group.label)
                : TopicNode(
                    id: group.slug,
                    label: group.label,
                    children: group.subcategories.map { TopicNode(id: $0.slug, label: $0.label) }
                  )
        }
    }
}
