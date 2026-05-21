from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

TEMPLATE_DIR = Path(__file__).parent / "templates"

_STRINGS = {
    "en": {
        "title":           "Stock AI Agent — Daily Report",
        "generated":       "Generated",
        "data_note":       "Data as of market close",
        "info_only":       "For informational purposes only",
        "summary":         "Summary",
        "col_ticker":      "Ticker",
        "col_price":       "Price",
        "col_rec":         "Rec.",
        "col_confidence":  "Confidence",
        "col_signal_score":"Signal Score",
        "col_sentiment":   "Sentiment",
        "col_feasibility": "Feasibility",
        "warn_low_conf":   "⚠ Low Confidence",
        "warn_repeated":   "🔁 Signal repeated — caution",
        "warn_data":       "📊 Data incomplete — consider ignoring",
        "warn_no_news":    "📰 No news data",
        "detail":          "Detail",
        "col_indicator":   "Indicator",
        "col_value":       "Value",
        "ind_price":       "Price",
        "ind_sma":         "SMA 20/50/200",
        "ind_macd":        "MACD / Signal",
        "ind_rsi":         "RSI (14)",
        "ind_bb":          "BB Upper/Lower",
        "ind_atr":         "ATR (14)",
        "ind_bullish":     "Bullish Signals",
        "ind_bearish":     "Bearish Signals",
        "ind_risk":        "Risk Factors",
        "ind_events":      "Key Events",
        "ind_levels":      "Support / Resistance",
        "ind_support":     "Support:",
        "ind_resistance":  "Resistance:",
        "accuracy_title":  "AI Historical Accuracy (30-day evaluation)",
        "col_total":       "Total",
        "col_correct":     "Correct",
        "col_accuracy":    "Accuracy",
        "footer": (
            "⚠ This report is for informational purposes only and does not constitute "
            "investment advice. Prices may not be real-time. "
            "Final investment decisions rest solely with the user."
        ),
    },
    "zh": {
        "title":           "股票AI助手 — 每日分析报告",
        "generated":       "生成于",
        "data_note":       "数据截止收盘",
        "info_only":       "仅供参考",
        "summary":         "摘要",
        "col_ticker":      "代码",
        "col_price":       "价格",
        "col_rec":         "建议",
        "col_confidence":  "置信度",
        "col_signal_score":"信号评分",
        "col_sentiment":   "情绪分",
        "col_feasibility": "可行性",
        "warn_low_conf":   "⚠ 置信度偏低",
        "warn_repeated":   "🔁 信号重复 — 请谨慎",
        "warn_data":       "📊 数据不完整 — 建议忽略",
        "warn_no_news":    "📰 无新闻数据",
        "detail":          "详情",
        "col_indicator":   "指标",
        "col_value":       "数值",
        "ind_price":       "价格",
        "ind_sma":         "均线 SMA 20/50/200",
        "ind_macd":        "MACD / 信号线",
        "ind_rsi":         "RSI (14)",
        "ind_bb":          "布林带上轨/下轨",
        "ind_atr":         "ATR (14)",
        "ind_bullish":     "看涨信号",
        "ind_bearish":     "看跌信号",
        "ind_risk":        "风险因素",
        "ind_events":      "关键事件",
        "ind_levels":      "支撑 / 阻力",
        "ind_support":     "支撑:",
        "ind_resistance":  "阻力:",
        "accuracy_title":  "AI 历史准确率（30天评估）",
        "col_total":       "总计",
        "col_correct":     "正确",
        "col_accuracy":    "准确率",
        "footer": (
            "⚠ 本报告仅供参考，不构成投资建议。价格可能非实时。"
            "最终投资决策由用户自行负责。"
        ),
    },
}

_ALERT_LABELS = {
    "en": {"morning": "Morning Alert",    "premarket": "Pre-Market Alert"},
    "zh": {"morning": "早盘预警",          "premarket": "盘前预警"},
}

_ALERT_COLS = {
    "en": ("Ticker", "Signal", "Confidence", "Score", "Rationale"),
    "zh": ("代码",   "建议",   "置信度",     "评分",  "可行性"),
}


class ReportRenderer:
    def __init__(self):
        self.env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)))

    def render(self, stocks: list[dict], accuracy_report: dict | None = None,
               lang: str = "en") -> str:
        tmpl = self.env.get_template("report.html")
        return tmpl.render(
            lang=lang,
            t=_STRINGS[lang],
            report_date=date.today().isoformat(),
            stocks=stocks,
            accuracy_report=accuracy_report or {},
        )

    def render_both(self, stocks: list[dict],
                    accuracy_report: dict | None = None) -> tuple[str, str]:
        """Returns (html_en, html_zh)."""
        return (
            self.render(stocks, accuracy_report, lang="en"),
            self.render(stocks, accuracy_report, lang="zh"),
        )

    def render_alert(self, stocks: list[dict], run_mode: str,
                     lang: str = "en") -> str:
        label = _ALERT_LABELS[lang].get(run_mode, "Alert")
        cols  = _ALERT_COLS[lang]
        rows  = "\n".join(
            f"<tr>"
            f"<td><b>{s['ticker']}</b></td>"
            f"<td style='color:{'#1a7a1a' if s.get('recommendation')=='BUY' else '#b00'}'>"
            f"{s.get('recommendation', '—')}</td>"
            f"<td>{(s.get('confidence') or 0):.0%}</td>"
            f"<td>{s.get('signal_score') or 0:.2f}</td>"
            f"<td style='color:#555;font-size:12px'>{s.get('feasibility', '')}</td>"
            f"</tr>"
            for s in stocks
        )
        return (
            f'<!DOCTYPE html><html lang="{lang}"><body '
            f'style="font-family:\'PingFang SC\',\'Microsoft YaHei\',sans-serif;padding:20px">'
            f"<h2 style='margin-bottom:4px'>{label}</h2>"
            f"<p style='color:#888;margin-top:0'>"
            f"{date.today().isoformat()} — {len(stocks)} signal(s)</p>"
            f"<table border='1' cellpadding='8' cellspacing='0' "
            f"style='border-collapse:collapse;min-width:500px'>"
            f"<tr style='background:#f5f5f5;text-align:left'>"
            f"<th>{cols[0]}</th><th>{cols[1]}</th><th>{cols[2]}</th>"
            f"<th>{cols[3]}</th><th>{cols[4]}</th></tr>"
            f"{rows}"
            f"</table></body></html>"
        )

    def render_alert_both(self, stocks: list[dict],
                          run_mode: str) -> tuple[str, str]:
        """Returns (html_en, html_zh)."""
        return (
            self.render_alert(stocks, run_mode, lang="en"),
            self.render_alert(stocks, run_mode, lang="zh"),
        )
