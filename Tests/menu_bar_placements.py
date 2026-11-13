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
    enum Key { case menuBarItemPlacements }
    static var stored: Data?
    static func set(_ data: Data, forKey: Key) { stored = data }
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
        var checked = 0
        for section in MenuBarSection.Name.allCases {
            let desired: [String]
            switch section {
            case .visible: desired = ["AH", "H", "A", "B", "C"]
            case .hidden: desired = ["AH", "A", "B", "C", "H"]
            case .alwaysHidden: desired = ["A", "B", "C", "AH", "H"]
            }
            for permutation in permutations(["A", "B", "C", "AH", "H"]) where permutation.firstIndex(of: "AH")! < permutation.firstIndex(of: "H")! {
                let harness = Harness()
                harness.load(desired)
                try harness.cacheAndSave()
                harness.observedItemWindows.removeAll()
                harness.load(permutation, generation: 2)
                try await harness.restore()
                precondition(harness.names == desired, "Wrong order: \(permutation) -> \(harness.names), expected \(desired)")
                checked += 1
            }
        }
        let harness = Harness()
        harness.load(["AH", "A", "B", "H", "C"])
        try harness.cacheAndSave()
        let stored = Defaults.stored!
        harness.savedPlacements = try JSONDecoder().decode([String: Harness.SavedPlacement].self, from: stored)
        harness.load(["AH", "A", "B", "H", "C"], generation: 2)
        harness.isMouseButtonDown = true
        let deferred = try await harness.restorePlacementIfNeeded(in: harness.windows.filter { $0.info.namespace != .ice }, hiddenControlItem: harness.windows.first { $0.info == .hiddenControlItem }!, alwaysHiddenControlItem: harness.windows.first { $0.info == .alwaysHiddenControlItem })
        precondition(deferred && harness.moves == 0, "Mouse interaction must defer restoration")
        harness.isMouseButtonDown = false
        try await harness.restore()
        let moves = harness.moves
        try await harness.restore()
        precondition(harness.moves == moves, "Unchanged window must not be restored twice")
        harness.load(["AH", "H", "B", "C", "A"], generation: 2)
        try harness.cacheAndSave()
        harness.load(["AH", "A", "B", "C", "H"], generation: 3)
        try await harness.restore()
        precondition(harness.names == ["AH", "H", "B", "C", "A"], "User placement must replace prior placement")
        let absent = Harness()
        absent.load(["AH", "A", "B", "H"])
        try absent.cacheAndSave()
        absent.load(["AH", "A", "H"], generation: 2)
        try await absent.restore()
        precondition(absent.names == ["AH", "A", "H"], "Missing neighbor should retain correct section")
        try absent.cacheAndSave()
        precondition(absent.savedPlacements["test.B:B"] != nil, "Missing applications must retain saved placements")
        let chain = Harness()
        chain.load(["AH", "A", "B", "C", "H"])
        try chain.cacheAndSave()
        chain.load(["AH", "A", "C", "H"])
        try chain.cacheAndSave()
        precondition(chain.savedPlacements["test.A:A"]?.rightNeighbor?.title == "B", "Quitting B must preserve A's link to B")
        chain.load(["AH", "C", "H"])
        try chain.cacheAndSave()
        chain.load(["AH", "C", "H", "B"], generation: 2)
        try await chain.restore()
        try chain.cacheAndSave()
        chain.load(["AH", "B", "C", "H", "A"], generation: 3)
        try await chain.restore()
        precondition(chain.names == ["AH", "A", "B", "C", "H"], "Sequential relaunches must preserve the original order")
        let temporary = Harness()
        temporary.load(["AH", "A", "B", "C", "H"])
        try temporary.cacheAndSave()
        temporary.load(["AH", "C", "H", "A", "B"], generation: 2)
        temporary.tempShownItemContexts = [TempShownItemContext(info: temporary.windows.last!.info)]
        let tempDeferred = try await temporary.restorePlacementIfNeeded(in: temporary.windows.filter { $0.info.namespace != .ice }, hiddenControlItem: temporary.windows.first { $0.info == .hiddenControlItem }!, alwaysHiddenControlItem: temporary.windows.first { $0.info == .alwaysHiddenControlItem })
        precondition(tempDeferred && temporary.moves == 0, "Temporarily shown neighbors must defer restoration")
        temporary.tempShownItemContexts.removeAll()
        try await temporary.restore()
        precondition(temporary.names == ["AH", "A", "B", "C", "H"], "Restore after temporary display ends")
        let failed = Harness()
        failed.load(["AH", "A", "H"])
        try failed.cacheAndSave()
        failed.load(["AH", "H", "A"], generation: 2)
        failed.failMove = true
        do {
            try await failed.restore()
            preconditionFailure("Expected movement failure")
        } catch {
            precondition(failed.observedItemWindows[failed.windows.last!.info] != failed.windows.last!.windowID, "Failed moves must remain retryable")
        }
        failed.failMove = false
        try await failed.restore()
        precondition(failed.names == ["AH", "A", "H"], "Retry must restore the saved section")
        print("PASS: \(checked) simultaneous-relaunch layouts across all sections; persistence, interaction deferral, stable windows, manual reorder, and missing applications")
    }
}
'''
# Type declarations belong outside the harness; only the production methods and state are nested.
program = (
    prelude[:prelude.index("final class Harness {")]
    + info + predicates + "\nfinal class Harness {\n"
    + block("    private struct SavedPlacement") + "\n"
    + state.split("    func slowMove", 1)[0] + "\n"
    + block("    private func savedRightNeighbor") + "\n"
    + block("    private func restorePlacementIfNeeded") + "\n"
    + block("    private func saveCurrentPlacements")
    + "\n    func slowMove" + state.split("    func slowMove", 1)[1]
)
with tempfile.TemporaryDirectory(prefix="ice-placement-tests-") as temp:
    path = pathlib.Path(temp)
    (path / "main.swift").write_text(program)
    subprocess.run(["swiftc", "-parse-as-library", str(path / "main.swift"), "-o", str(path / "tests")], check=True)
    subprocess.run([str(path / "tests")], check=True)
