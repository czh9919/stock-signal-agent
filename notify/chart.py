"""
Risk-return chart generator.
risk_return_png(frontier, lang) → PNG bytes, or b"" if matplotlib unavailable.
"""
import io
import logging

logger = logging.getLogger(__name__)

_FONT_ZH = "Microsoft YaHei"


def risk_return_png(frontier: dict, lang: str = "en") -> bytes:
    """Render efficient-frontier PNG. Returns empty bytes on failure."""
    if not frontier:
        return b""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.font_manager as fm
        import numpy as np
    except ImportError:
        logger.warning("matplotlib not available — chart skipped")
        return b""
    try:
        return _render(frontier, lang, plt, fm)
    except Exception as e:
        logger.warning(f"Chart render failed: {e}")
        return b""


def _render(frontier: dict, lang: str, plt, fm) -> bytes:
    is_zh = (lang == "zh")

    # Use Microsoft YaHei for Chinese; fall back to default if unavailable
    available = {f.name for f in fm.fontManager.ttflist}
    rc = {"font.family": _FONT_ZH} if (is_zh and _FONT_ZH in available) else {}

    L = {
        "title":   "风险–收益图" if is_zh else "Risk–Return Chart",
        "x":       "年化波动率 σ" if is_zh else "Annualised Volatility σ",
        "y":       "年化收益率 E(R)" if is_zh else "Annualised Return E(R)",
        "current": "当前" if is_zh else "Current",
        "optimal": "最优" if is_zh else "Optimal",
        "sharpe":  "夏普比率" if is_zh else "Sharpe Ratio",
    }

    with plt.rc_context(rc):
        fig, ax = plt.subplots(figsize=(5.2, 3.4), dpi=108)
        fig.patch.set_facecolor("#fafafa")
        ax.set_facecolor("#fafafa")

        # Monte Carlo scatter coloured by Sharpe
        mc = frontier.get("mc", [])
        if mc:
            vols    = [r[0] for r in mc]
            rets    = [r[1] for r in mc]
            sharpes = [r[2] for r in mc]
            sc = ax.scatter(vols, rets, c=sharpes, cmap="RdYlGn",
                            s=5, alpha=0.35, vmin=-1, vmax=3, rasterized=True)
            cbar = fig.colorbar(sc, ax=ax, fraction=0.028, pad=0.02)
            cbar.set_label(L["sharpe"], fontsize=7)
            cbar.ax.tick_params(labelsize=6)

        # Individual asset dots + labels
        for asset in frontier.get("assets", []):
            ax.scatter(asset["vol"], asset["ret"],
                       color="#5a7fcf", s=45, zorder=4, edgecolors="#fff", linewidths=0.8)
            ax.annotate(asset["ticker"], (asset["vol"], asset["ret"]),
                        textcoords="offset points", xytext=(5, 3),
                        fontsize=7, color="#333")

        # Current portfolio
        cur = frontier.get("current", {})
        if cur:
            ax.scatter(cur["vol"], cur["ret"], color="#e67e22", s=110, zorder=5,
                       marker="o", edgecolors="#fff", linewidths=1.5)
            ax.annotate(L["current"], (cur["vol"], cur["ret"]),
                        textcoords="offset points", xytext=(6, -13),
                        fontsize=8, color="#e67e22", fontweight="bold")

        # Optimal (max-Sharpe) portfolio
        opt = frontier.get("optimal")
        if opt:
            ax.scatter(opt["vol"], opt["ret"], color="#27ae60", s=120, zorder=5,
                       marker="D", edgecolors="#fff", linewidths=1.5)
            ax.annotate(L["optimal"], (opt["vol"], opt["ret"]),
                        textcoords="offset points", xytext=(6, 5),
                        fontsize=8, color="#27ae60", fontweight="bold")

        ax.set_xlabel(L["x"], fontsize=9)
        ax.set_ylabel(L["y"], fontsize=9)
        ax.set_title(L["title"], fontsize=11, fontweight="bold", color="#1a1a2e")
        ax.tick_params(labelsize=7)
        ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v*100:.0f}%"))
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v*100:.0f}%"))
        ax.grid(True, alpha=0.25, linewidth=0.5)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

        plt.tight_layout(pad=0.8)
        buf = io.BytesIO()
        fig.savefig(buf, format="png", bbox_inches="tight", dpi=108)
        plt.close(fig)
        return buf.getvalue()
