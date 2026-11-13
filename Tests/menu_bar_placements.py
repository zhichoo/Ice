#!/usr/bin/env python3
"""Exercise production placement methods with a deterministic menu bar, using Swift."""
import pathlib
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
source = (ROOT / "Ice/MenuBar/MenuBarItems/MenuBarItemManager.swift").read_text()


def block(start):
    begin = source.index(start)
    opening = source.index("{", begin)
    depth = 1
    end = opening + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[begin:end].replace("private ", "")


info = (ROOT / "Ice/MenuBar/MenuBarItems/MenuBarItemInfo.swift").read_text()
predicates = (ROOT / "Ice/Utilities/Predicates.swift").read_text()
predicates = predicates[:predicates.index("// MARK: - Window Predicates")] + predicates[
    predicates.index("extension Predicates where Input == MenuBarItem"):
    predicates.index("// MARK: - Control Item Predicates")
]
prelude = r'''
import Cocoa

enum Constants { static let bundleIdentifier = "com.jordanbaird.Ice" }
enum ControlItem {
    enum Identifier: String { case iceIcon, hidden, alwaysHidden }
}
typealias CGWindowID = UInt32
struct MenuBarItem {
    let info: MenuBarItemInfo
    let windowID: CGWindowID
    var frame: CGRect
    var isMovable = true
    var canBeHidden = true
}
enum MoveDestination { case leftOfItem(MenuBarItem), rightOfItem(MenuBarItem) }
enum MenuBarSection { enum Name: CaseIterable { case visible, hidden, alwaysHidden } }
enum Defaults {
    enum Key { case menuBarItemPlacements, knownMenuBarItems }
    static var stored: Data?
    static func set(_ data: Data, forKey key: Key) {
        if case .menuBarItemPlacements = key { stored = data }
    }
}
struct ItemCache {
    var sections = [MenuBarSection.Name: [MenuBarItem]]()
    func managedItems(for section: MenuBarSection.Name) -> [MenuBarItem] {
        sections[section, default: []].filter { $0.canBeHidden }
    }
    var managedItems: [MenuBarItem] {
        MenuBarSection.Name.allCases.flatMap { managedItems(for: $0) }
    }
}
struct TempShownItemContext { let info: MenuBarItemInfo }
final class Harness {
'''
state = r'''
    var savedPlacements = [String: SavedPlacement]()
    var observedItemWindows = [MenuBarItemInfo: CGWindowID]()
    var knownItemInfos: Set<MenuBarItemInfo>? = []
    var itemCache = ItemCache()
    var isMouseButtonDown = false
    var mouseHasRecentlyMoved = false
    var tempShownItemContexts = [TempShownItemContext]()
    var windows = [MenuBarItem]()
    var moves = 0
    var failMove = false

    func slowMove(item: MenuBarItem, to destination: MoveDestination) async throws {
        if failMove { throw NSError(domain: "test", code: 1) }
        let index = windows.firstIndex { $0.windowID == item.windowID }!
        let moving = windows.remove(at: index)
        switch destination {
        case .leftOfItem(let target):
            windows.insert(moving, at: windows.firstIndex { $0.windowID == target.windowID }!)
        case .rightOfItem(let target):
            windows.insert(moving, at: windows.firstIndex { $0.windowID == target.windowID }! + 1)
        }
        refreshFrames()
        moves += 1
    }
    func refreshFrames() {
        for index in windows.indices {
            windows[index].frame = CGRect(x: index * 10, y: 0, width: 10, height: 10)
        }
    }
    func load(_ names: [String], generation: UInt32 = 1) {
        windows = names.enumerated().map { index, name in
            let info: MenuBarItemInfo
            switch name {
            case "H": info = .hiddenControlItem
            case "AH": info = .alwaysHiddenControlItem
            default: info = MenuBarItemInfo(namespace: .init("test." + name), title: name)
            }
            return MenuBarItem(info: info, windowID: generation * 1000 + name.utf8.reduce(0) { $0 + UInt32($1) }, frame: .zero)
        }
        refreshFrames()
    }
    func cacheAndSave() throws {
        let predicates = Predicates.sectionPredicates(
            hiddenControlItem: windows.first { $0.info == .hiddenControlItem }!,
            alwaysHiddenControlItem: windows.first { $0.info == .alwaysHiddenControlItem }
        )
        let items = windows.filter { $0.info.namespace != .ice }
        itemCache.sections[.visible] = items.filter(predicates.isInVisibleSection)
        itemCache.sections[.hidden] = items.filter(predicates.isInHiddenSection)
        itemCache.sections[.alwaysHidden] = items.filter(predicates.isInAlwaysHiddenSection)
        try saveCurrentPlacements()
    }
    func restore() async throws {
        for _ in 0..<30 {
            let moved = try await restorePlacementIfNeeded(
                in: windows.filter { $0.info.namespace != .ice },
                hiddenControlItem: windows.first { $0.info == .hiddenControlItem }!,
                alwaysHiddenControlItem: windows.first { $0.info == .alwaysHiddenControlItem }
            )
            if !moved { return }
        }
        fatalError("Restoration did not converge")
    }
    func placeNew() async throws -> Bool {
        try await placeNewItemIfNeeded(
            in: windows.filter { $0.info.namespace != .ice },
            hiddenControlItem: windows.first { $0.info == .hiddenControlItem }!
        )
    }
    var names: [String] { windows.map { $0.info == .hiddenControlItem ? "H" : $0.info == .alwaysHiddenControlItem ? "AH" : $0.info.title } }
}

func permutations(_ values: [String]) -> [[String]] {
    if values.isEmpty { return [[]] }
    return values.indices.flatMap { index -> [[String]] in
        var rest = values
        let first = rest.remove(at: index)
        return permutations(rest).map { [first] + $0 }
    }
}

@main struct Tests {
    static func main() async throws {
        var restored = 0
        for section in MenuBarSection.Name.allCases {
            let baseline: [String]
            switch section {
            case .visible: baseline = ["AH", "H", "A", "B", "C"]
            case .hidden: baseline = ["AH", "A", "B", "C", "H"]
            case .alwaysHidden: baseline = ["A", "B", "C", "AH", "H"]
            }
            for permutation in permutations(["A", "B", "C", "AH", "H"]) where permutation.firstIndex(of: "AH")! < permutation.firstIndex(of: "H")! {
                let harness = Harness()
                harness.load(baseline)
                try harness.cacheAndSave()
                let expected = harness.savedPlacements
                harness.load(permutation, generation: 2)
                try await harness.restore()
                try harness.cacheAndSave()
                precondition(harness.savedPlacements == expected, "Wrong section: \(permutation) -> \(harness.names)")
                restored += 1
            }
        }
        var newLayouts = 0
        for permutation in permutations(["A", "B", "N", "AH", "H"]) where permutation.firstIndex(of: "AH")! < permutation.firstIndex(of: "H")! {
            let harness = Harness()
            harness.load(permutation)
            harness.knownItemInfos = Set(harness.windows.filter { ["A", "B"].contains($0.info.title) }.map(\.info))
            let placed = try await harness.placeNew()
            precondition(placed, "New item must be processed")
            let index = harness.names.firstIndex(of: "N")!
            precondition(index > harness.names.firstIndex(of: "H")!, "New item must never default to a hidden section")
            precondition(index == harness.names.count - 1, "New item must append after existing movable items")
            let placedAgain = try await harness.placeNew()
            precondition(!placedAgain, "Recognized items must not be repeatedly appended")
            try harness.cacheAndSave()
            precondition(harness.savedPlacements["test.N:N"]?.section == "visible", "Default visible section must be persisted")
            newLayouts += 1
        }
        let stable = Harness()
        stable.load(["AH", "H", "A", "B", "C"])
        try stable.cacheAndSave()
        stable.load(["AH", "H", "C", "A", "B"], generation: 2)
        try await stable.restore()
        precondition(stable.moves == 0 && stable.names == ["AH", "H", "C", "A", "B"], "Correct sections must not trigger order restoration")
        stable.load(["A", "AH", "H", "B", "C"], generation: 3)
        try await stable.restore()
        precondition(stable.names == ["AH", "H", "B", "C", "A"], "Returning visible item must append at the end")

        let legacy = Harness()
        let legacyData = Data(#"{"test.A:A":{"section":"visible","rightNeighbor":"test.B:B"}}"#.utf8)
        legacy.savedPlacements = try JSONDecoder().decode([String: Harness.SavedPlacement].self, from: legacyData)
        legacy.load(["A", "AH", "H", "B"])
        try await legacy.restore()
        precondition(legacy.names == ["AH", "H", "B", "A"], "Old data must preserve sections while ignoring saved order")

        let baseline = Harness()
        baseline.load(["AH", "A", "H", "B"])
        baseline.knownItemInfos = nil
        let adopted = try await baseline.placeNew()
        precondition(!adopted && baseline.moves == 0, "First scan must preserve the existing user layout")
        try baseline.cacheAndSave()
        let stored = Defaults.stored!
        baseline.savedPlacements = try JSONDecoder().decode([String: Harness.SavedPlacement].self, from: stored)
        baseline.load(["AH", "H", "A", "B"], generation: 2)
        baseline.isMouseButtonDown = true
        let deferred = try await baseline.restorePlacementIfNeeded(in: baseline.windows.filter { $0.info.namespace != .ice }, hiddenControlItem: baseline.windows.first { $0.info == .hiddenControlItem }!, alwaysHiddenControlItem: baseline.windows.first { $0.info == .alwaysHiddenControlItem })
        precondition(deferred && baseline.moves == 0, "Mouse interaction must defer restoration")
        baseline.isMouseButtonDown = false
        // Simulate a completed user drag overriding a saved hidden section.
        let dragged = baseline.windows.first { $0.info.title == "A" }!
        baseline.observedItemWindows[dragged.info] = dragged.windowID
        try await baseline.restore()
        precondition(baseline.moves == 0, "User drag must take priority over a saved section")
        try baseline.cacheAndSave()
        precondition(baseline.savedPlacements["test.A:A"]?.section == "visible", "User changes must be remembered")
        baseline.load(["AH", "H", "B"], generation: 3)
        try baseline.cacheAndSave()
        precondition(baseline.savedPlacements["test.A:A"]?.section == "visible", "Absent applications must retain their section")

        let fixed = Harness()
        fixed.load(["N", "AH", "H", "C"])
        fixed.windows[3].isMovable = false
        fixed.knownItemInfos = Set([fixed.windows[3].info])
        _ = try await fixed.placeNew()
        precondition(fixed.names == ["AH", "H", "N", "C"], "Append before fixed system items when no movable anchor exists")

        let temporary = Harness()
        temporary.load(["AH", "A", "H"])
        try temporary.cacheAndSave()
        temporary.load(["AH", "H", "A"], generation: 2)
        temporary.tempShownItemContexts = [TempShownItemContext(info: temporary.windows.last!.info)]
        let tempDeferred = try await temporary.restorePlacementIfNeeded(in: temporary.windows.filter { $0.info.namespace != .ice }, hiddenControlItem: temporary.windows.first { $0.info == .hiddenControlItem }!, alwaysHiddenControlItem: temporary.windows.first { $0.info == .alwaysHiddenControlItem })
        precondition(tempDeferred && temporary.moves == 0, "Temporarily shown items must not be restored early")
        temporary.tempShownItemContexts.removeAll()
        temporary.failMove = true
        do {
            try await temporary.restore()
            preconditionFailure("Expected movement failure")
        } catch {
            precondition(temporary.observedItemWindows[temporary.windows.last!.info] != temporary.windows.last!.windowID, "Failed moves must remain retryable")
        }
        temporary.failMove = false
        try await temporary.restore()
        precondition(temporary.names == ["AH", "A", "H"], "Retry must restore the saved section")
        print("PASS: \(restored) section-restoration layouts; \(newLayouts) new-item layouts always append visibly; legacy data, no automatic reorder, manual changes, fixed items, temporary display, and retries")
    }
}
'''
# Type declarations belong outside the harness; only the production methods and state are nested.
program = (
    prelude[:prelude.index("final class Harness {")]
    + info + predicates + "\nfinal class Harness {\n"
    + block("    private struct SavedPlacement") + "\n"
    + state.split("    func slowMove", 1)[0] + "\n"
    + block("    private func saveKnownItemInfos") + "\n"
    + block("    private func visibleSectionEnd") + "\n"
    + block("    private func placeNewItemIfNeeded") + "\n"
    + block("    private func restorePlacementIfNeeded") + "\n"
    + block("    private func saveCurrentPlacements")
    + "\n    func slowMove" + state.split("    func slowMove", 1)[1]
)
with tempfile.TemporaryDirectory(prefix="ice-placement-tests-") as temp:
    path = pathlib.Path(temp)
    (path / "main.swift").write_text(program)
    subprocess.run(["swiftc", "-parse-as-library", str(path / "main.swift"), "-o", str(path / "tests")], check=True)
    subprocess.run([str(path / "tests")], check=True)
