import XCTest
@testable import ReelTranscodeApp

final class PerformanceProfileTests: XCTestCase {
    func testBalancedProfileMapping() {
        let values = PerformanceProfile.balanced.appliedConcurrency()
        XCTAssertEqual(values.maxWorkers, 2)
        XCTAssertEqual(values.ioNiceSleep, 0.0)
    }

    func testLowImpactProfileMapping() {
        let values = PerformanceProfile.lowImpact.appliedConcurrency()
        XCTAssertEqual(values.maxWorkers, 1)
        XCTAssertEqual(values.ioNiceSleep, 0.25)
    }
}
