"""The source agents' filings. Plain data, kept apart from the MCP server so anything can read it
without importing (and configuring logging for) the MCP stack."""

FEEDS = {
    "trusted": [
        ("Acme Logistics LLC", "C4829301", "2024-03-15", "CA"),
        ("Birch Freight Inc", "C4829302", "2024-04-02", "CA"),
        ("Cedar Holdings LLC", "D1000234", "2024-04-09", "DE"),   # outside this connector's jurisdiction
        ("Dune Supply Co", "C4829303", "2024-13-45", "CA"),       # malformed formation date
        ("Elm Partners LP", "C4829304", "2024-05-21", "CA"),
        ("Fjord Imports Corp", "C4829305", "2024-06-30", "CA"),
        ("Granite Works LLC", "C4829306", "2024-07-11", "CA"),
        ("Harbor Cold Storage Inc", "C4829307", "2024-08-19", "CA"),
        ("Iris Medical Supply LLC", "C4829308", "2024-09-03", "CA"),
        ("Juniper Rail Services Inc", "C4829309", "2024-09-27", "CA"),
    ],
    "rogue": [
        ("Shell Corp Inc", "C9999991", "2024-01-01", "CA"),
        ("Phantom Trading LLC", "C9999992", "2024-01-02", "CA"),
        ("Mirage Capital Inc", "C9999993", "2024-01-03", "CA"),
        ("Ghost Freight LLC", "C9999994", "2024-01-04", "CA"),
        ("Vapor Holdings Corp", "C9999995", "2024-01-05", "CA"),
        ("Echo Ventures LLC", "C9999996", "2024-01-06", "CA"),
    ],
}
