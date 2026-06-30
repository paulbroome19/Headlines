//
//  TopicSeed.swift
//  Headlines
//
//  PLACEHOLDER taxonomy for the filter tree. This is the single, easily
//  swappable source of tree data — the real category list (driven by the
//  backend YAML taxonomy) will replace `defaultTree()` without touching
//  TopicNode.swift or FilterTreeView.swift.
//
//  Defaults: a few top categories ship pre-lit so a user who taps nothing
//  still gets a sensible briefing.
//

import Foundation

enum TopicSeed {

    /// The fresh-install tree, with World / UK / Business pre-selected.
    static func defaultTree() -> [TopicNode] {
        [
            TopicNode(id: "world",      label: "WORLD",      isOn: true),
            TopicNode(id: "uk",         label: "UK",         isOn: true),
            TopicNode(id: "business",   label: "BUSINESS",   isOn: true),
            TopicNode(id: "technology", label: "TECHNOLOGY", isOn: false),
            TopicNode(id: "sport", label: "SPORT", children: [
                TopicNode(id: "sport.football", label: "FOOTBALL", children: [
                    TopicNode(id: "sport.football.manutd",    label: "MAN UNITED"),
                    TopicNode(id: "sport.football.arsenal",   label: "ARSENAL"),
                    TopicNode(id: "sport.football.liverpool", label: "LIVERPOOL"),
                ]),
                TopicNode(id: "sport.cricket", label: "CRICKET"),
                TopicNode(id: "sport.f1",      label: "F1"),
            ]),
            TopicNode(id: "culture",    label: "CULTURE",    isOn: false),
        ]
    }
}
