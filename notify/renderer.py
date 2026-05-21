import os
from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

TEMPLATE_DIR = Path(__file__).parent / "templates"


class ReportRenderer:
    def __init__(self):
        self.env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)))

    def render(self, stocks: list[dict], accuracy_report: dict | None = None) -> str:
        tmpl = self.env.get_template("report.html")
        return tmpl.render(
            report_date=date.today().isoformat(),
            stocks=stocks,
            accuracy_report=accuracy_report or {},
        )
