import Foundation
import Testing
@testable import VaultKit

private func releases(_ list: [(tag: String, draft: Bool, digest: String?)]) -> Data {
    let items = list.map { r in
        let digest = r.digest.map { ",\"digest\":\"sha256:\($0)\"" } ?? ""
        return """
        {"tag_name":"\(r.tag)","draft":\(r.draft),"assets":[
          {"name":"DocumentVault-macOS.dmg","size":5,"browser_download_url":"https://example.invalid/\(r.tag)/DocumentVault-macOS.dmg"\(digest)}]}
        """
    }
    return Data("[\(items.joined(separator: ","))]".utf8)
}

@Test func comparesVersionsLikePeopleDo() {
    #expect(Updates.compare("0.9.1", "0.9.0-beta") > 0)
    #expect(Updates.compare("0.9.0", "0.9.0-beta") > 0)
    #expect(Updates.compare("0.10.0", "0.9.9") > 0)
    #expect(Updates.compare("0.9.1", "0.9.1") == 0)
    #expect(Updates.compare("0.9.0-beta", "0.9.1") < 0)
}

@Test func picksTheNewestReleaseWithACheckableDMG() {
    let json = releases([("v0.9.0-beta", false, "aa"), ("v0.9.1", false, "bb"), ("v0.9.3", true, "cc"), ("v0.9.2", false, nil)])
    let update = Updates.newest(in: json, current: "0.9.0-beta", asset: "DocumentVault-macOS.dmg")
    #expect(update?.version == "0.9.1")  // 0.9.3 is a draft; 0.9.2 has nothing to check it against
    #expect(update?.sha256 == "bb")
    #expect(Updates.newest(in: json, current: "0.9.1", asset: "DocumentVault-macOS.dmg") == nil)
}
