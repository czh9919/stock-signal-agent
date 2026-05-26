"""
M5 — Bilingual mobile-first HTML email report.
All CSS is inline (Gmail strips <style> blocks).
Max output target: < 100 KB.
"""
from datetime import date
from typing import Optional


_RAG_COLOR  = {"RED": "#c0392b", "AMBER": "#e67e22", "GREEN": "#27ae60", "GREY": "#95a5a6"}
_RAG_BG     = {"RED": "#fdf2f2", "AMBER": "#fef9f0", "GREEN": "#f0fdf4", "GREY": "#f5f5f5"}
_RAG_ZH     = {"RED": "红", "AMBER": "黄", "GREEN": "绿", "GREY": "灰"}

_S = "font-family:-apple-system,BlinkMacSystemFont,'PingFang SC','Microsoft YaHei',sans-serif;"
_CARD = "display:inline-block;width:130px;padding:10px 12px;margin:4px;border-radius:8px;background:#f8f9ff;vertical-align:top;"

_TIER_COLOR = {1: "#c0392b", 2: "#e67e22", 3: "#95a5a6"}


def _pct(v, decimals=1):
    if v is None or v != v:
        return "N/A"
    return f"{v*100:.{decimals}f}%"

def _gbp(v):
    if v is None or v != v:
        return "N/A"
    sign = "-" if v < 0 else ""
    return f"{sign}£{abs(v):,.0f}"

def _f(v, decimals=2):
    if v is None or v != v:
        return "N/A"
    return f"{v:.{decimals}f}"

def _delta(now, prev):
    if now is None or prev is None or now != now or prev != prev:
        return ""
    d = now - prev
    sign = "▲" if d >= 0 else "▼"
    color = "#27ae60" if d >= 0 else "#c0392b"
    return f' <span style="color:{color};font-size:11px">{sign}{abs(d)*100:.1f}pp</span>'


def build_report(metrics: dict, stress: dict, holdings: list[dict],
                 price_data: dict, last_week: Optional[dict] = None,
                 lang: str = "both",
                 frontier: Optional[dict] = None,
                 suggestions: Optional[list] = None,
                 has_chart_en: bool = False,
                 has_chart_zh: bool = False) -> tuple[str, str]:
    """Returns (html_en, html_zh)."""
    en = _build(metrics, stress, holdings, price_data, last_week, "en",
                frontier, suggestions, has_chart=has_chart_en)
    zh = _build(metrics, stress, holdings, price_data, last_week, "zh",
                frontier, suggestions, has_chart=has_chart_zh)
    return en, zh


def _build(metrics, stress, holdings, price_data, last_week, lang,
           frontier=None, suggestions=None, has_chart=False):
    t        = _T[lang]
    rag      = metrics.get("overall_rag", "GREY")
    rag_col  = _RAG_COLOR[rag]
    rag_bg   = _RAG_BG[rag]
    today    = date.today().isoformat()
    rag_label = _RAG_ZH[rag] if lang == "zh" else rag

    prev_metrics = (last_week or {}).get("metrics", {})

    sections = []

    # ── Header ─────────────────────────────────────────────────────────────────
    sections.append(f"""
<div style="{_S}background:linear-gradient(135deg,#1a1a2e,#16213e);padding:20px 24px;border-radius:12px 12px 0 0">
  <h1 style="margin:0 0 4px;font-size:20px;color:#fff">{t['title']}</h1>
  <p style="margin:0;font-size:12px;color:rgba(255,255,255,.65)">{t['generated']} {today}</p>
  <div style="margin-top:10px;display:inline-block;padding:4px 14px;border-radius:20px;
       background:{rag_col};color:#fff;font-weight:700;font-size:13px">
    {t['risk_level']}: {rag_label}
  </div>
</div>""")

    # ── Alert banner ───────────────────────────────────────────────────────────
    alert_map = metrics.get("alerts", {})
    active_alerts = [k for k, v in alert_map.items() if v == "RED"]
    if active_alerts:
        al_text = " &nbsp;|&nbsp; ".join(
            t['alert_labels'].get(k, k) for k in active_alerts
        )
        sections.append(f"""
<div style="background:#fdf2f2;border-left:4px solid #c0392b;padding:10px 16px;margin:0">
  <strong style="color:#c0392b">{t['alert_banner']}</strong>
  <br><span style="font-size:13px;color:#555">{al_text}</span>
</div>""")

    # ── Snapshot cards ─────────────────────────────────────────────────────────
    nav      = metrics.get("nav_gbp", 0)
    pnl      = metrics.get("total_pnl_gbp", 0)
    var95    = metrics.get("var_95_ewma")
    sharpe   = metrics.get("sharpe")
    pnl_col  = "#27ae60" if (pnl or 0) >= 0 else "#c0392b"

    bond_holdings  = [h for h in holdings if h.get("asset_class") == "bond"]
    bond_nav       = sum(h["market_value_gbp"] for h in bond_holdings)
    bond_pct       = bond_nav / nav if nav else 0.0
    bond_card      = ""
    if bond_holdings:
        bond_card = f"""
    <div style="{_CARD}">
      <div style="font-size:11px;color:#888">{t['bond_exposure']}</div>
      <div style="font-size:18px;font-weight:700;color:#2c3e50">{_pct(bond_pct)}</div>
      <div style="font-size:11px;color:#aaa">{_gbp(bond_nav)}</div>
    </div>"""

    sections.append(f"""
<div style="padding:16px 20px">
  <h2 style="font-size:14px;color:#444;margin:0 0 10px;border-bottom:1px solid #eee;padding-bottom:6px">
    {t['snapshot']}
  </h2>
  <div>
    <div style="{_CARD}">
      <div style="font-size:11px;color:#888">{t['nav']}</div>
      <div style="font-size:18px;font-weight:700;color:#1a1a2e">{_gbp(nav)}</div>
    </div>
    <div style="{_CARD}">
      <div style="font-size:11px;color:#888">{t['pnl']}</div>
      <div style="font-size:18px;font-weight:700;color:{pnl_col}">{_gbp(pnl)}</div>
    </div>
    <div style="{_CARD}">
      <div style="font-size:11px;color:#888">{t['var95']}</div>
      <div style="font-size:18px;font-weight:700;color:{_RAG_COLOR[alert_map.get('var_95','GREY')]}">{_pct(var95)}</div>
    </div>
    <div style="{_CARD}">
      <div style="font-size:11px;color:#888">{t['sharpe']}</div>
      <div style="font-size:18px;font-weight:700;color:{_RAG_COLOR[alert_map.get('sharpe','GREY')]}">{_f(sharpe)}</div>
    </div>{bond_card}
  </div>
</div>""")

    # ── Risk-return chart (CID inline image) ───────────────────────────────────
    if has_chart and frontier:
        sections.append(f"""
<div style="padding:0 20px 16px">
  <h2 style="font-size:14px;color:#444;margin:0 0 10px;border-bottom:1px solid #eee;padding-bottom:6px">
    {t['rr_chart']}
  </h2>
  <img src="cid:rrChart" alt="{t['rr_chart']}"
       style="max-width:100%;display:block;border-radius:8px;border:1px solid #eee">
  <p style="font-size:11px;color:#aaa;margin:6px 0 0">{t['rr_chart_note']}</p>
</div>""")

    # ── Top 3 worst stress scenarios ───────────────────────────────────────────
    top3 = (stress or {}).get("top3_worst", [])
    if top3:
        rows = ""
        for s in top3:
            rows += (
                f"<tr>"
                f"<td style='padding:6px 8px;border-bottom:1px solid #f0f0f0'>"
                f"<b>{s['name']}</b><br><span style='color:#888;font-size:11px'>{s['name_zh']}</span></td>"
                f"<td style='padding:6px 8px;border-bottom:1px solid #f0f0f0;color:#c0392b'>{_gbp(s['gbp_loss'])}</td>"
                f"<td style='padding:6px 8px;border-bottom:1px solid #f0f0f0;color:#c0392b'>{_pct(s['pct_loss'])}</td>"
                f"</tr>"
            )
        sections.append(f"""
<div style="padding:0 20px 16px">
  <h2 style="font-size:14px;color:#444;margin:0 0 10px;border-bottom:1px solid #eee;padding-bottom:6px">
    {t['stress_top3']}
  </h2>
  <table style="width:100%;border-collapse:collapse;font-size:13px">
    <tr style="background:#f8f9ff">
      <th style="padding:6px 8px;text-align:left;font-weight:600;color:#666">{t['scenario']}</th>
      <th style="padding:6px 8px;text-align:left;font-weight:600;color:#666">{t['gbp_loss']}</th>
      <th style="padding:6px 8px;text-align:left;font-weight:600;color:#666">{t['pct_loss']}</th>
    </tr>{rows}
  </table>
</div>""")

    # ── Risk indicators table with descriptions ────────────────────────────────
    prev_m = prev_metrics
    # Each row: (key, en_label, zh_label, val, prev_val, (en_desc, zh_desc))
    risk_rows = [
        ("var_95",   "VaR (95%)",    "VaR（95%）",
         _pct(metrics.get("var_95_ewma")),   _pct(prev_m.get("var_95_ewma")),
         ("Worst-case 1-in-20 daily loss as % of NAV",
          "1/20概率最差日损失，占总市值的比例")),
        ("var_99",   "VaR (99%)",    "VaR（99%）",
         _pct(metrics.get("var_99_ewma")),   _pct(prev_m.get("var_99_ewma")),
         ("Worst-case 1-in-100 daily loss as % of NAV",
          "1/100概率最差日损失，占总市值的比例")),
        ("cvar",     "CVaR (95%)",   "CVaR（95%）",
         _pct(metrics.get("cvar_95")),        _pct(prev_m.get("cvar_95")),
         ("Average loss in the worst 5% of outcomes (tail risk)",
          "最差5%情景下的平均损失（尾部风险）")),
        ("max_dd",   "Max Drawdown", "最大回撤",
         _pct(metrics.get("max_drawdown")),  _pct(prev_m.get("max_drawdown")),
         ("Largest peak-to-trough decline in the period",
          "区间内最大峰谷回撤幅度")),
        ("sharpe",   "Sharpe Ratio", "夏普比率",
         _f(metrics.get("sharpe")),           _f(prev_m.get("sharpe")),
         ("Excess return per unit of risk; >1 = good, <0.5 = poor",
          "每单位风险的超额收益；>1为良好，<0.5为较差")),
        ("beta",     "Beta",         "贝塔系数",
         _f(metrics.get("beta")),             _f(prev_m.get("beta")),
         ("Portfolio sensitivity to the market (S&P 500); 1 = tracks index",
          "对市场（标普500）的敏感度；1=与指数同步波动")),
        ("hhi",      "HHI",          "HHI集中度",
         _f(metrics.get("hhi")),              _f(prev_m.get("hhi")),
         ("Concentration index: 0 = fully diversified, 1 = single position",
          "集中度指数：0=完全分散，1=单一持仓")),
        ("max_pos",  "Max Position", "最大单仓",
         _pct(metrics.get("max_position_wt")),_pct(prev_m.get("max_position_wt")),
         ("Largest single holding as % of total NAV",
          "最大单一持仓占总市值的比例")),
        ("port_vol", "Portfolio σ",  "组合波动率",
         _pct(metrics.get("port_sigma_annual")),_pct(prev_m.get("port_sigma_annual")),
         ("Annualised portfolio volatility (standard deviation of returns)",
          "年化组合波动率（收益率标准差）")),
    ]
    risk_html = ""
    for key, en_label, zh_label, val, prev_val, descs in risk_rows:
        rag_key = {"var_95":"var_95","var_99":"var_95","cvar":"cvar_ratio",
                   "max_dd":"max_dd","sharpe":"sharpe","beta":"beta",
                   "hhi":"hhi","max_pos":"max_pos","port_vol":"var_95"}.get(key, key)
        r_color  = _RAG_COLOR.get(alert_map.get(rag_key, "GREY"), "#888")
        label    = zh_label if lang == "zh" else f"{en_label}（{zh_label}）"
        desc     = descs[1] if lang == "zh" else descs[0]
        prev_tag = ""
        if prev_val not in ("N/A", None, ""):
            prev_tag = f" <span style='color:#bbb;font-size:10px'>prev {prev_val}</span>"
        risk_html += (
            f"<tr><td style='padding:7px 8px;border-bottom:1px solid #f0f0f0;font-size:13px'>"
            f"<span style='font-weight:600'>{label}</span>"
            f"<br><span style='color:#aaa;font-size:10px'>{desc}</span></td>"
            f"<td style='padding:7px 8px;border-bottom:1px solid #f0f0f0;font-size:13px;"
            f"color:{r_color};font-weight:600;white-space:nowrap'>{val}{prev_tag}</td></tr>"
        )

    sections.append(f"""
<div style="padding:0 20px 16px">
  <h2 style="font-size:14px;color:#444;margin:0 0 10px;border-bottom:1px solid #eee;padding-bottom:6px">
    {t['risk_indicators']}
  </h2>
  <table style="width:100%;border-collapse:collapse">
    <tr style="background:#f8f9ff">
      <th style="padding:6px 8px;text-align:left;font-weight:600;color:#666;font-size:13px">{t['metric']}</th>
      <th style="padding:6px 8px;text-align:left;font-weight:600;color:#666;font-size:13px">{t['value']}</th>
    </tr>{risk_html}
  </table>
</div>""")

    # ── Rebalancing suggestions ────────────────────────────────────────────────
    if suggestions:
        sug_rows = ""
        tier_labels = {
            1: (t['tier_act'],   _TIER_COLOR[1]),
            2: (t['tier_watch'], _TIER_COLOR[2]),
            3: (t['tier_hold'],  _TIER_COLOR[3]),
        }
        for s in suggestions:
            tier      = s["tier"]
            lbl, col  = tier_labels.get(tier, ("", "#888"))
            badge     = (f"<span style='background:{col};color:#fff;padding:2px 7px;"
                         f"border-radius:10px;font-size:11px;font-weight:700'>{lbl}</span>")
            direction = s["direction_zh"] if lang == "zh" else s["direction"]
            dir_col   = "#27ae60" if s["delta"] > 0 else "#c0392b"
            cgt_tag   = (f" <span style='color:#e67e22;font-size:10px'>{t['cgt_flag']}</span>"
                         if s.get("cgt_flag") else "")
            sug_rows += (
                f"<tr style='background:{'#fff8f8' if tier == 1 else '#fff'}'>"
                f"<td style='padding:6px 8px;border-bottom:1px solid #f0f0f0'>{badge}</td>"
                f"<td style='padding:6px 8px;border-bottom:1px solid #f0f0f0;font-size:13px'>"
                f"<b>{s['ticker']}</b>{cgt_tag}</td>"
                f"<td style='padding:6px 8px;border-bottom:1px solid #f0f0f0;font-size:12px;color:#555'>"
                f"{_pct(s['cur_weight'])}</td>"
                f"<td style='padding:6px 8px;border-bottom:1px solid #f0f0f0;font-size:12px;color:#555'>"
                f"{_pct(s['opt_weight'])}</td>"
                f"<td style='padding:6px 8px;border-bottom:1px solid #f0f0f0;font-size:12px;"
                f"color:{dir_col};font-weight:600'>{direction} {_pct(abs(s['delta']))}</td>"
                f"<td style='padding:6px 8px;border-bottom:1px solid #f0f0f0;font-size:12px;color:#555'>"
                f"{_gbp(s['gbp_change'])}</td>"
                f"</tr>"
            )
        sections.append(f"""
<div style="padding:0 20px 16px">
  <h2 style="font-size:14px;color:#444;margin:0 0 10px;border-bottom:1px solid #eee;padding-bottom:6px">
    {t['rebalance']}
  </h2>
  <p style="font-size:11px;color:#aaa;margin:0 0 8px">{t['rebalance_note']}</p>
  <table style="width:100%;border-collapse:collapse">
    <tr style="background:#f8f9ff">
      <th style="padding:5px 8px;text-align:left;font-size:12px;color:#666">{t['action']}</th>
      <th style="padding:5px 8px;text-align:left;font-size:12px;color:#666">{t['ticker']}</th>
      <th style="padding:5px 8px;text-align:left;font-size:12px;color:#666">{t['cur_wt']}</th>
      <th style="padding:5px 8px;text-align:left;font-size:12px;color:#666">{t['opt_wt']}</th>
      <th style="padding:5px 8px;text-align:left;font-size:12px;color:#666">{t['change']}</th>
      <th style="padding:5px 8px;text-align:left;font-size:12px;color:#666">{t['gbp_chg']}</th>
    </tr>{sug_rows}
  </table>
</div>""")

    # ── Top 10 equity positions ────────────────────────────────────────────────
    equity_h = [h for h in holdings if h.get("asset_class", "equity") == "equity"]
    top10    = sorted(equity_h, key=lambda h: h["market_value_gbp"], reverse=True)[:10]
    pos_rows = ""
    for h in top10:
        pnl_pct = h["unrealised_pnl_gbp"] / h["cost_basis_gbp"] if h.get("cost_basis_gbp") else 0
        c       = "#27ae60" if pnl_pct >= 0 else "#c0392b"
        pos_rows += (
            f"<tr>"
            f"<td style='padding:5px 8px;border-bottom:1px solid #f0f0f0;font-size:12px'><b>{h['ticker']}</b></td>"
            f"<td style='padding:5px 8px;border-bottom:1px solid #f0f0f0;font-size:12px;color:#555'>{h['platform']}</td>"
            f"<td style='padding:5px 8px;border-bottom:1px solid #f0f0f0;font-size:12px'>{_pct(h['weight'])}</td>"
            f"<td style='padding:5px 8px;border-bottom:1px solid #f0f0f0;font-size:12px;color:{c}'>{_pct(pnl_pct)}</td>"
            f"</tr>"
        )

    sections.append(f"""
<div style="padding:0 20px 16px">
  <h2 style="font-size:14px;color:#444;margin:0 0 10px;border-bottom:1px solid #eee;padding-bottom:6px">
    {t['top10']}
  </h2>
  <table style="width:100%;border-collapse:collapse">
    <tr style="background:#f8f9ff">
      <th style="padding:5px 8px;text-align:left;font-size:12px;color:#666">{t['ticker']}</th>
      <th style="padding:5px 8px;text-align:left;font-size:12px;color:#666">{t['platform']}</th>
      <th style="padding:5px 8px;text-align:left;font-size:12px;color:#666">{t['weight']}</th>
      <th style="padding:5px 8px;text-align:left;font-size:12px;color:#666">{t['pnl_pct']}</th>
    </tr>{pos_rows}
  </table>
</div>""")

    # ── Bond holdings ──────────────────────────────────────────────────────────
    if bond_holdings:
        bond_rows = ""
        for h in sorted(bond_holdings, key=lambda h: h["market_value_gbp"], reverse=True):
            desc    = h.get("description") or h["ticker"]
            mat     = h.get("maturity", "")
            cpn_raw = h.get("coupon", "")
            try:
                cpn_str = f"{float(cpn_raw):.3f}%" if cpn_raw else ""
            except (ValueError, TypeError):
                cpn_str = str(cpn_raw)
            detail  = " · ".join(filter(None, [cpn_str, mat]))
            pnl_pct = h["unrealised_pnl_gbp"] / h["cost_basis_gbp"] if h.get("cost_basis_gbp") else 0
            c       = "#27ae60" if pnl_pct >= 0 else "#c0392b"
            detail_html = (f"<br><span style='color:#888;font-size:11px'>{detail}</span>"
                           if detail else "")
            bond_rows += (
                f"<tr>"
                f"<td style='padding:5px 8px;border-bottom:1px solid #f0f0f0;font-size:12px'>"
                f"<b>{desc}</b>{detail_html}"
                f"</td>"
                f"<td style='padding:5px 8px;border-bottom:1px solid #f0f0f0;font-size:12px;color:#555'>{h['platform']}</td>"
                f"<td style='padding:5px 8px;border-bottom:1px solid #f0f0f0;font-size:12px'>{_pct(h['weight'])}</td>"
                f"<td style='padding:5px 8px;border-bottom:1px solid #f0f0f0;font-size:12px'>{_gbp(h['market_value_gbp'])}</td>"
                f"<td style='padding:5px 8px;border-bottom:1px solid #f0f0f0;font-size:12px;color:{c}'>{_pct(pnl_pct)}</td>"
                f"</tr>"
            )
        sections.append(f"""
<div style="padding:0 20px 16px">
  <h2 style="font-size:14px;color:#444;margin:0 0 10px;border-bottom:1px solid #eee;padding-bottom:6px">
    {t['bond_holdings']}
  </h2>
  <table style="width:100%;border-collapse:collapse">
    <tr style="background:#f8f9ff">
      <th style="padding:5px 8px;text-align:left;font-size:12px;color:#666">{t['bond_desc']}</th>
      <th style="padding:5px 8px;text-align:left;font-size:12px;color:#666">{t['platform']}</th>
      <th style="padding:5px 8px;text-align:left;font-size:12px;color:#666">{t['weight']}</th>
      <th style="padding:5px 8px;text-align:left;font-size:12px;color:#666">{t['mkt_val']}</th>
      <th style="padding:5px 8px;text-align:left;font-size:12px;color:#666">{t['pnl_pct']}</th>
    </tr>{bond_rows}
  </table>
</div>""")

    # ── Monte Carlo ────────────────────────────────────────────────────────────
    mc = (stress or {}).get("monte_carlo", {})
    if mc:
        sections.append(f"""
<div style="padding:0 20px 16px">
  <h2 style="font-size:14px;color:#444;margin:0 0 10px;border-bottom:1px solid #eee;padding-bottom:6px">
    {t['monte_carlo']}
  </h2>
  <p style="font-size:13px;color:#555;margin:4px 0">
    {mc.get('paths',0):,} {t['mc_paths']} · {mc.get('horizon_days',30)}{t['mc_days']}
  </p>
  <p style="font-size:13px;margin:4px 0">
    VaR(95%) <b style="color:#c0392b">{_pct(mc.get('var_95'))}</b> ({_gbp(mc.get('gbp_var_95'))})&nbsp;&nbsp;
    CVaR <b style="color:#c0392b">{_pct(mc.get('cvar_95'))}</b>&nbsp;&nbsp;
    P(+) <b style="color:#27ae60">{_pct(mc.get('p_positive'))}</b>
  </p>
</div>""")

    # ── Correlation breakdown ──────────────────────────────────────────────────
    cb = (stress or {}).get("corr_breakdown", {})
    if cb:
        decay = cb.get("diversification_decay")
        decay_color = "#c0392b" if decay and decay > 1.3 else "#27ae60"
        sections.append(f"""
<div style="padding:0 20px 16px">
  <h2 style="font-size:14px;color:#444;margin:0 0 10px;border-bottom:1px solid #eee;padding-bottom:6px">
    {t['corr_breakdown']}
  </h2>
  <p style="font-size:13px;color:#555;margin:4px 0">
    {t['normal_sigma']} <b>{_pct(cb.get('normal_sigma'))}</b> &nbsp;→&nbsp;
    {t['crisis_sigma']} <b style="color:#c0392b">{_pct(cb.get('crisis_sigma'))}</b><br>
    {t['decay_ratio']} <b style="color:{decay_color}">{_f(decay)}×</b>
  </p>
</div>""")

    # ── Data quality footer ────────────────────────────────────────────────────
    excluded = [tk for tk, pd_obj in price_data.items() if pd_obj.flag]
    flags_html = ""
    if excluded:
        flags_html = "".join(
            f"<div style='font-size:11px;color:#888'>{tk} {price_data[tk].flag}</div>"
            for tk in excluded
        )

    sections.append(f"""
<div style="padding:12px 20px;background:#f8f9ff;border-top:1px solid #eee;border-radius:0 0 12px 12px">
  <p style="font-size:11px;color:#aaa;margin:0 0 4px">{t['footer_note']}</p>
  {flags_html}
</div>""")

    body = "\n".join(sections)
    html = f"""<!DOCTYPE html>
<html lang="{lang}">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{t['title']}</title></head>
<body style="{_S}background:#f5f6fa;margin:0;padding:16px">
<div style="max-width:600px;margin:0 auto;background:#fff;border-radius:12px;
     box-shadow:0 2px 12px rgba(0,0,0,.08);overflow:hidden">
{body}
</div>
</body></html>"""
    return html


# ── i18n strings ──────────────────────────────────────────────────────────────

_T = {
    "en": {
        "title":          "Portfolio Risk Report",
        "generated":      "Generated",
        "risk_level":     "Risk Level",
        "alert_banner":   "⚠ Active Alerts",
        "snapshot":       "Snapshot",
        "nav":            "Total NAV",
        "pnl":            "Unrealised P&L",
        "var95":          "VaR (95%)",
        "sharpe":         "Sharpe Ratio",
        "stress_top3":    "Stress Tests — Top 3 Worst Scenarios",
        "scenario":       "Scenario",
        "gbp_loss":       "Est. Loss",
        "pct_loss":       "% Loss",
        "rr_chart":       "Risk–Return Chart",
        "rr_chart_note":  "Orange circle = current portfolio · Green diamond = max-Sharpe optimal · Dots = individual positions",
        "risk_indicators":"Risk Indicators",
        "metric":         "Metric",
        "value":          "Value",
        "rebalance":      "Portfolio Rebalancing Suggestions",
        "rebalance_note": "Act: |Δ|≥5% · Watch: 2–5% or CGT applies · Hold: |Δ|<2%",
        "action":         "Action",
        "cur_wt":         "Current",
        "opt_wt":         "Optimal",
        "change":         "Direction",
        "gbp_chg":        "Est. GBP",
        "tier_act":       "Act",
        "tier_watch":     "Watch",
        "tier_hold":      "Hold",
        "cgt_flag":       "⚑ CGT",
        "top10":          "Top 10 Positions",
        "ticker":         "Ticker",
        "platform":       "Platform",
        "weight":         "Weight",
        "pnl_pct":        "P&L%",
        "monte_carlo":    "Monte Carlo Simulation",
        "mc_paths":       "paths",
        "mc_days":        "-day horizon",
        "corr_breakdown": "Correlation Breakdown",
        "normal_sigma":   "Normal σ:",
        "crisis_sigma":   "Crisis σ:",
        "decay_ratio":    "Diversification decay:",
        "bond_exposure":  "Bond Exposure",
        "bond_holdings":  "Bond Holdings",
        "bond_desc":      "Description",
        "mkt_val":        "Market Value",
        "footer_note":    "For informational purposes only. Not investment advice.",
        "alert_labels": {
            "var_95":    "VaR (95%) > 5% NAV",
            "cvar_ratio":"CVaR/VaR ratio > 1.8×",
            "max_dd":    "Max Drawdown > 20%",
            "max_pos":   "Single position > 30%",
            "hhi":       "HHI > 0.25",
            "beta":      "Beta > 1.5",
            "sharpe":    "Sharpe < 0.5",
            "daily_loss":"Single-day loss > 3%",
        },
    },
    "zh": {
        "title":          "投资组合风险报告",
        "generated":      "生成于",
        "risk_level":     "风险等级",
        "alert_banner":   "⚠ 当前预警",
        "snapshot":       "核心指标快照",
        "nav":            "总市值",
        "pnl":            "未实现盈亏",
        "var95":          "VaR（95%）",
        "sharpe":         "夏普比率",
        "stress_top3":    "压力测试 — 最差三个情景",
        "scenario":       "情景",
        "gbp_loss":       "预估损失",
        "pct_loss":       "损失比例",
        "rr_chart":       "风险–收益图",
        "rr_chart_note":  "橙圈=当前组合 · 绿菱=最优组合（最大夏普） · 蓝点=单个持仓",
        "risk_indicators":"风险指标",
        "metric":         "指标",
        "value":          "数值",
        "rebalance":      "组合再平衡建议",
        "rebalance_note": "立即行动：|Δ|≥5% · 观察：2–5%或涉及资本利得税 · 维持：|Δ|<2%",
        "action":         "行动",
        "cur_wt":         "当前权重",
        "opt_wt":         "最优权重",
        "change":         "方向",
        "gbp_chg":        "预估金额",
        "tier_act":       "立即行动",
        "tier_watch":     "观察",
        "tier_hold":      "维持",
        "cgt_flag":       "⚑ CGT",
        "top10":          "前十大持仓",
        "ticker":         "代码",
        "platform":       "平台",
        "weight":         "权重",
        "pnl_pct":        "盈亏%",
        "monte_carlo":    "蒙特卡洛模拟",
        "mc_paths":       "条路径",
        "mc_days":        "天期限",
        "corr_breakdown": "相关性击穿分析",
        "normal_sigma":   "正常市场σ:",
        "crisis_sigma":   "危机市场σ:",
        "decay_ratio":    "分散化折损:",
        "bond_exposure":  "债券敞口",
        "bond_holdings":  "债券持仓",
        "bond_desc":      "债券名称",
        "mkt_val":        "市值",
        "footer_note":    "本报告仅供参考，不构成投资建议。",
        "alert_labels": {
            "var_95":    "VaR(95%) > 组合市值5%",
            "cvar_ratio":"CVaR/VaR > 1.8倍",
            "max_dd":    "最大回撤 > 20%",
            "max_pos":   "单仓权重 > 30%",
            "hhi":       "HHI > 0.25",
            "beta":      "贝塔 > 1.5",
            "sharpe":    "夏普 < 0.5",
            "daily_loss":"单日亏损 > 3%",
        },
    },
}
