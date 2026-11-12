from app.data_sources.connectors.base import BookmakerConnector

BOOKMAKERS = ["BetPawa", "Betika", "Fans Sport", "SportPesa", "1xBet"]

# Bookmakers whose own public site feed is ingested by the refresh job.
PUBLIC_FEEDS = {
    "BetPawa": "BETPAWA",
    "Betika": "BETIKA",
    "SportPesa": "SPORTPESA",
    "Fans Sport": "FANSPORT",
}


class PublicFeedConnector(BookmakerConnector):
    """Uses the bookmaker's public JSON feed; the refresh job stores the prices."""

    configured = True

    def __init__(self, bookmaker_name: str, source: str):
        self.bookmaker_name = bookmaker_name
        self.source = source
        self.notes = "Public feed read by the refresh job; prices carry source and capture time."

    def fetch_fixtures(self) -> list[dict]:
        # Refreshing happens in the ingest job so one request is shared by every
        # consumer; status reads the tracked count from the database instead.
        return []


class NotConfiguredConnector(BookmakerConnector):
    configured = False
    source = None

    def __init__(self, bookmaker_name: str):
        self.bookmaker_name = bookmaker_name
        self.notes = "No permitted feed configured; returns no data rather than inventing it."

    def fetch_fixtures(self) -> list[dict]:
        return []


def get_connectors():
    return [
        PublicFeedConnector(name, source) if (source := PUBLIC_FEEDS.get(name)) else NotConfiguredConnector(name)
        for name in BOOKMAKERS
    ]
