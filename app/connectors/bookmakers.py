from app.connectors.base import BookmakerConnector

BOOKMAKERS = ["BetPawa", "Betika", "Fans Sport", "SportPesa", "1xBet"]

class NotConfiguredConnector(BookmakerConnector):
    def __init__(self, bookmaker_name: str):
        self.bookmaker_name = bookmaker_name

    def fetch_fixtures(self) -> list[dict]:
        # No public/authorized feed configured yet. Return no data rather than
        # inventing fixtures or scraping a site without permission.
        return []

def get_connectors():
    return [NotConfiguredConnector(name) for name in BOOKMAKERS]
