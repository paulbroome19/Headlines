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

    /// Tap the REAL Home Play button (centre, on the seam ~61% down) and check
    /// whether the now-playing screen actually presents. Closes the prior gap
    /// where only the player VIEW was tested, never the Home BUTTON.
    func testHomePlayButtonPresentsPlayer() throws {
        let app = launchToHome()
        attach("home-before-tap", app.debugDescription)

        // The play button sits at screen centre-x, ~61% down (on the seam).
        app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.61)).tap()

        // The player (loading "PREPARING YOUR BRIEFING", failed "COULDN'T LOAD
        // BRIEFING", or the "… BRIEFING" masthead) should appear within a few s.
        let briefing = app.staticTexts.containing(
            NSPredicate(format: "label CONTAINS[c] %@", "BRIEFING")
        ).firstMatch
        let appeared = briefing.waitForExistence(timeout: 10)

        attach("home-after-play-tap", app.debugDescription)
        XCTAssertTrue(appeared, "Play tap did NOT present the player. Screen after tap is attached.")
    }

    /// Tap the REAL Home Profile "P" (top-right) and record whether any profile
    /// screen presents.
    func testHomeProfileButtonOpensProfile() throws {
        let app = launchToHome()

        app.coordinate(withNormalizedOffset: CGVector(dx: 0.9, dy: 0.11)).tap()
        sleep(2)

        attach("home-after-profile-tap", app.debugDescription)
        // A profile/edit screen would contain a Save/Cancel button or a "Name" field.
        let opened = app.navigationBars.firstMatch.waitForExistence(timeout: 4)
            || app.buttons["Save"].exists || app.buttons["Cancel"].exists
        XCTAssertTrue(opened, "Profile tap did NOT open any profile screen. Screen after tap is attached.")
    }
}
