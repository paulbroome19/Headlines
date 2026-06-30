//
//  HeadlinesUITests.swift
//  HeadlinesUITests
//

import XCTest

final class HeadlinesUITests: XCTestCase {

    override func setUpWithError() throws {
        continueAfterFailure = true
    }

    /// Launch straight to Home (skip onboarding) via the UserDefaults argument domain.
    private func launchToHome() -> XCUIApplication {
        let app = XCUIApplication()
        app.launchArguments = [
            "-didOnboard", "1",
            "-profileId", "1",
            "-userName", "Paul",
            "-hasSeenHomeHint", "1",
        ]
        app.launch()
        sleep(5)   // wait out the split-flap loader → Home
        return app
    }

    private func attach(_ name: String, _ text: String) {
        let a = XCTAttachment(string: text)
        a.name = name
        a.lifetime = .keepAlways
        add(a)
    }

    /// Tap the REAL Home "Assemble your briefing" arrow (docked at the base of
    /// the greeting card) and check the now-playing screen actually presents.
    /// Taps by accessibility label so it's robust to the C6 layout.
    func testHomePlayButtonPresentsPlayer() throws {
        let app = launchToHome()
        attach("home-before-tap", app.debugDescription)

        app.buttons["Assemble your briefing"].firstMatch.tap()

        // The player (loading "PREPARING YOUR BRIEFING", failed "COULDN'T LOAD
        // BRIEFING", or the "… BRIEFING" masthead) should appear within a few s.
        let briefing = app.staticTexts.containing(
            NSPredicate(format: "label CONTAINS[c] %@", "BRIEFING")
        ).firstMatch
        let appeared = briefing.waitForExistence(timeout: 10)

        attach("home-after-play-tap", app.debugDescription)
        XCTAssertTrue(appeared, "Arrow tap did NOT present the player. Screen after tap is attached.")
    }

    /// Tap the REAL Home Profile "P" (masthead, top-right) and check the light
    /// settings filters screen (ProfileFiltersView) presents.
    func testHomeProfileButtonOpensProfile() throws {
        let app = launchToHome()

        app.buttons["Profile"].firstMatch.tap()

        // ProfileFiltersView shows "LOADING YOUR FILTERS", then the tree (CTA
        // "DONE") or a "… CATEGORIES" / "… FILTERS" state — all light-register,
        // no nav bar. Any of these means the settings filters opened.
        let pred = NSPredicate(format: "label CONTAINS[c] %@ OR label CONTAINS[c] %@",
                               "filters", "categories")
        let opened = app.staticTexts.containing(pred).firstMatch.waitForExistence(timeout: 8)
            || app.buttons["DONE"].waitForExistence(timeout: 1)

        attach("home-after-profile-tap", app.debugDescription)
        XCTAssertTrue(opened, "Profile tap did NOT open the settings filters. Screen after tap is attached.")
    }
}
