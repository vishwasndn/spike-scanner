#!/usr/bin/env python3
"""
=============================================================================
Daily NASDAQ Spike Scanner — "Fade the Spike" Strategy
=============================================================================
Runs daily via GitHub Actions. Scans for NASDAQ stocks that spiked 30%+ today,
scores each with the Fade Score (0-14), and outputs:
  1. data/today.json — today's fade candidates for the dashboard
  2. data/history.json — rolling 90-day history of all scanned spikes
  3. Sends email alert with top fade candidates

Based on analysis of 3,713 spike events over 2 years:
  - 69% of spiked penny stocks decline within 1 month
  - Fade Score 9+ stocks decline 80% of the time (median -31.8%)
  - Optimal short window: first 2-4 weeks after spike

Requirements: pip install yfinance pandas numpy
=============================================================================
"""

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import json
import os
import sys
import time
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import warnings
warnings.filterwarnings('ignore')


# ─── CONFIGURATION ──────────────────────────────────────────────────────────
SPIKE_THRESHOLD = 0.30
MAX_PRICE = 20.0
MIN_VOLUME = 100000
HISTORY_DAYS = 90
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


# ─── HELPER: Safe numeric conversions ───────────────────────────────────────
def safe_float(val, default=0.0):
    if val is None:
        return default
    try:
        v = float(val)
        return default if (np.isnan(v) or np.isinf(v)) else v
    except (TypeError, ValueError):
        return default


def safe_val(val):
    """Make a value JSON-serializable (convert NaN/None to null)."""
    if val is None:
        return None
    if isinstance(val, float) and (np.isnan(val) or np.isinf(val)):
        return None
    if isinstance(val, (np.integer,)):
        return int(val)
    if isinstance(val, (np.floating,)):
        return float(val)
    return val


# ─── STEP 1: GET TODAY'S TOP GAINERS ────────────────────────────────────────
def get_todays_gainers():
    """
    Fetch today's NASDAQ stocks that gained 30%+ using multiple approaches.
    """
    print("📊 Fetching today's market data...")

    gainers = []

    # Approach 1: Screen known volatile tickers + broad NASDAQ universe
    tickers = set()

    # Get NASDAQ tickers from GitHub
    try:
        import urllib.request
        url = "https://raw.githubusercontent.com/rreichel3/US-Stock-Symbols/main/nasdaq/nasdaq_tickers.txt"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        response = urllib.request.urlopen(req, timeout=15)
        data = response.read().decode('utf-8')
        for line in data.strip().split('\n'):
            t = line.strip()
            if t and len(t) <= 5 and t.isalpha():
                tickers.add(t)
        print(f"   ✓ Loaded {len(tickers)} NASDAQ tickers")
    except Exception as e:
        print(f"   ⚠ Could not load ticker list: {e}")

    # Add curated volatile stocks as safety net
    curated = [
        "SAVA","ATOS","CLOV","WISH","SOFI","PLTR","NKLA","WKHS","GOEV","RIDE",
        "QS","BLNK","PLUG","FCEL","CLNE","MVST","OPEN","SKLZ","HIMS","ACHR",
        "JOBY","LILM","SKIN","OUST","LMND","ROOT","ATER","SDC","IONQ","RGTI",
        "QUBT","KULR","SMCI","MARA","RIOT","BITF","HUT","CIFR","CLSK","SOUN",
        "NIO","XPEV","LI","LCID","RIVN","FFIE","MULN","CHPT","EVGO","SNDL",
        "TLRY","CGC","ACB","BBAI","BFLY","AI","UPST","PATH","RCAT","LUNR",
        "APLD","IREN","WULF","ASTS","RKLB",
    ]
    for t in curated:
        tickers.add(t)

    tickers = sorted(list(tickers))
    print(f"   Total universe: {len(tickers)} tickers")

    # Download today + yesterday data (2 days) to compute daily change
    end_date = datetime.now()
    start_date = end_date - timedelta(days=7)  # Extra buffer for weekends/holidays

    print("   Downloading price data...")
    batch_size = 100
    all_data = {}

    for i in range(0, len(tickers), batch_size):
        batch = tickers[i:i + batch_size]
        try:
            data = yf.download(
                " ".join(batch),
                start=start_date.strftime("%Y-%m-%d"),
                end=end_date.strftime("%Y-%m-%d"),
                group_by="ticker",
                auto_adjust=True,
                threads=True,
                progress=False,
            )
            if data.empty:
                continue

            if len(batch) == 1:
                ticker = batch[0]
                if not data.empty and len(data) >= 2:
                    all_data[ticker] = data[['Open', 'High', 'Low', 'Close', 'Volume']].copy()
            else:
                for ticker in batch:
                    try:
                        if ticker in data.columns.get_level_values(0):
                            df = data[ticker][['Open', 'High', 'Low', 'Close', 'Volume']].dropna()
                            if len(df) >= 2:
                                all_data[ticker] = df.copy()
                    except Exception:
                        pass
        except Exception:
            pass

        if i + batch_size < len(tickers):
            time.sleep(1)

    print(f"   ✓ Got data for {len(all_data)} stocks")

    # Find today's (most recent trading day) gainers
    for ticker, df in all_data.items():
        try:
            if len(df) < 2:
                continue

            latest = df.iloc[-1]
            prev = df.iloc[-2]

            daily_return = (latest['Close'] - prev['Close']) / prev['Close']

            if daily_return >= SPIKE_THRESHOLD and latest['Close'] <= MAX_PRICE and latest['Volume'] >= MIN_VOLUME:
                # Compute 20-day avg volume
                avg_vol = df['Volume'].iloc[:-1].mean() if len(df) > 2 else latest['Volume']
                avg_vol = safe_float(avg_vol, latest['Volume'])  # Handle NaN from mean()
                vol_ratio = latest['Volume'] / avg_vol if avg_vol > 0 else 1

                gap_up = (latest['Open'] - prev['Close']) / prev['Close'] * 100 if prev['Close'] > 0 else 0
                close_vs_high = (latest['Close'] - latest['High']) / latest['High'] * 100 if latest['High'] > 0 else 0

                gainers.append({
                    'ticker': ticker,
                    'date': df.index[-1].strftime('%Y-%m-%d'),
                    'close': round(float(latest['Close']), 2),
                    'prev_close': round(float(prev['Close']), 2),
                    'daily_gain_pct': round(daily_return * 100, 1),
                    'volume': int(safe_float(latest['Volume'], 0)),
                    'avg_volume': int(safe_float(avg_vol, 0)),
                    'volume_ratio': round(vol_ratio, 1),
                    'gap_up_pct': round(gap_up, 1),
                    'close_vs_high_pct': round(close_vs_high, 1),
                    'open': round(float(latest['Open']), 2),
                    'high': round(float(latest['High']), 2),
                    'low': round(float(latest['Low']), 2),
                })
        except Exception:
            continue

    print(f"   🔥 Found {len(gainers)} stocks that spiked 30%+ today")
    return gainers


# ─── STEP 2: FETCH PROFILES & COMPUTE FADE SCORE ────────────────────────────
def score_gainers(gainers):
    """
    Fetch fundamental profiles and compute Fade Score for each gainer.
    """
    if not gainers:
        return []

    print(f"\n🧬 Scoring {len(gainers)} gainers with Fade Score...")

    scored = []
    for i, g in enumerate(gainers):
        ticker = g['ticker']
        print(f"   [{i+1}/{len(gainers)}] {ticker}...", end=" ", flush=True)

        # Initialize profile with safe defaults
        profile = {
            'company_name': ticker,
            'sector': 'Unknown',
            'industry': 'Unknown',
            'market_cap': 0,
            'profit_margin': 0,
            'revenue': 0,
            'eps': 0,
            'pe': 0,
            'short_pct_float': 0,
            'institution_pct': 0,
            'insider_pct': 0,
            'float_shares': 0,
            'revenue_growth': 0,
        }

        try:
            stock = yf.Ticker(ticker)

            # fast_info (reliable)
            try:
                fi = stock.fast_info
                profile['market_cap'] = safe_float(getattr(fi, 'market_cap', None), 0)
            except Exception:
                pass

            # full info (may fail)
            try:
                info = stock.info
                if info and len(info) > 5:
                    profile['company_name'] = info.get('shortName', info.get('longName', ticker))
                    profile['sector'] = info.get('sector', 'Unknown')
                    profile['industry'] = info.get('industry', 'Unknown')
                    profile['profit_margin'] = safe_float(info.get('profitMargins'), 0)
                    profile['revenue'] = safe_float(info.get('totalRevenue'), 0)
                    profile['eps'] = safe_float(info.get('trailingEps'), 0)
                    profile['pe'] = safe_float(info.get('trailingPE'), 0)
                    profile['short_pct_float'] = safe_float(info.get('shortPercentOfFloat'), 0)
                    profile['institution_pct'] = safe_float(info.get('heldPercentInstitutions'), 0)
                    profile['insider_pct'] = safe_float(info.get('heldPercentInsiders'), 0)
                    profile['float_shares'] = safe_float(info.get('floatShares'), 0)
                    profile['revenue_growth'] = safe_float(info.get('revenueGrowth'), 0)
            except Exception:
                pass
        except Exception:
            pass

        # ── Categorize market cap ──
        mc = profile.get('market_cap', 0)
        if mc < 50_000_000:
            mc_cat = 'Nano (<50M)'
        elif mc < 300_000_000:
            mc_cat = 'Micro (50-300M)'
        elif mc < 2_000_000_000:
            mc_cat = 'Small (300M-2B)'
        elif mc < 10_000_000_000:
            mc_cat = 'Mid (2B-10B)'
        else:
            mc_cat = 'Large (10B+)'

        is_profitable = 1 if profile.get('profit_margin', 0) > 0 else 0
        has_revenue = 1 if profile.get('revenue', 0) > 0 else 0

        # ── COMPUTE FADE SCORE (0-14) ──
        fade_score = 0
        fade_reasons = []

        # +2: Nano or micro cap
        if mc < 300_000_000:
            fade_score += 2
            fade_reasons.append("Nano/micro cap")

        # +2: Unprofitable
        if not is_profitable:
            fade_score += 2
            fade_reasons.append("Unprofitable")

        # +2: Intraday selloff (close >10% below high)
        if g['close_vs_high_pct'] < -10:
            fade_score += 2
            fade_reasons.append("Intraday selloff")

        # +2: Spike >75% (+1 more if >100%)
        if g['daily_gain_pct'] > 75:
            fade_score += 2
            fade_reasons.append("Spike >75%")
        if g['daily_gain_pct'] > 100:
            fade_score += 1
            fade_reasons.append("Spike >100%")

        # +1: Volume ratio >10x
        if g['volume_ratio'] > 10:
            fade_score += 1
            fade_reasons.append("Volume >10x avg")

        # +1: Gap-up >25%
        if g['gap_up_pct'] > 25:
            fade_score += 1
            fade_reasons.append("Large gap-up")

        # +1: Short interest >15%
        if profile.get('short_pct_float', 0) > 0.15:
            fade_score += 1
            fade_reasons.append("High short interest")

        # +1: Low institutional ownership
        if profile.get('institution_pct', 0) < 0.15:
            fade_score += 1
            fade_reasons.append("Low institutions")

        # +1: No revenue
        if not has_revenue:
            fade_score += 1
            fade_reasons.append("No revenue")

        # Probability estimate based on our 2-year analysis
        if fade_score >= 9:
            fade_probability = 80
        elif fade_score >= 6:
            fade_probability = 70
        elif fade_score >= 3:
            fade_probability = 63
        else:
            fade_probability = 49

        # Format market cap
        if mc >= 1e9:
            mc_str = f"${mc/1e9:.1f}B"
        elif mc >= 1e6:
            mc_str = f"${mc/1e6:.0f}M"
        else:
            mc_str = f"${mc:,.0f}"

        result = {
            **g,
            'company_name': profile.get('company_name', ticker),
            'sector': profile.get('sector', 'Unknown'),
            'industry': profile.get('industry', 'Unknown'),
            'market_cap': safe_val(mc),
            'market_cap_str': mc_str,
            'market_cap_category': mc_cat,
            'is_profitable': is_profitable,
            'has_revenue': has_revenue,
            'profit_margin': safe_val(profile.get('profit_margin', 0)),
            'pe_ratio': safe_val(profile.get('pe', 0)),
            'eps': safe_val(profile.get('eps', 0)),
            'short_pct_float': safe_val(profile.get('short_pct_float', 0)),
            'institution_pct': safe_val(profile.get('institution_pct', 0)),
            'insider_pct': safe_val(profile.get('insider_pct', 0)),
            'revenue_growth': safe_val(profile.get('revenue_growth', 0)),
            'fade_score': fade_score,
            'fade_probability': fade_probability,
            'fade_reasons': fade_reasons,
            'scan_timestamp': datetime.now().isoformat(),
        }

        scored.append(result)
        print(f"Fade Score: {fade_score}/14 ({fade_probability}% fade prob)")

        time.sleep(0.5)  # Rate limit

    # Sort by fade score (highest first)
    scored.sort(key=lambda x: x['fade_score'], reverse=True)
    return scored


# ─── STEP 3: UPDATE HISTORY ─────────────────────────────────────────────────
def update_history(today_data):
    """
    Maintain a rolling 90-day history of all scanned spikes.
    Also tracks forward performance of previously scanned stocks.
    """
    history_path = os.path.join(DATA_DIR, "history.json")

    # Load existing history
    if os.path.exists(history_path):
        with open(history_path, 'r') as f:
            history = json.load(f)
    else:
        history = []

    # Add today's data
    history.extend(today_data)

    # Remove entries older than HISTORY_DAYS
    cutoff = (datetime.now() - timedelta(days=HISTORY_DAYS)).strftime('%Y-%m-%d')
    history = [h for h in history if (h.get('date') or '1900-01-01') >= cutoff]

    # Track forward performance of past picks
    print("\n📈 Tracking forward performance of past picks...")
    tickers_to_check = set()
    for h in history:
        if h.get('date', '') < datetime.now().strftime('%Y-%m-%d'):
            tickers_to_check.add(h['ticker'])

    if tickers_to_check:
        try:
            prices = yf.download(
                " ".join(list(tickers_to_check)[:50]),  # Limit to 50
                period="1d",
                auto_adjust=True,
                threads=True,
                progress=False,
            )
            if not prices.empty:
                for h in history:
                    # Only update if we haven't already done so (don't re-update old entries)
                    if 'return_since_spike' in h and h.get('date', '') < datetime.now().strftime('%Y-%m-%d'):
                        continue

                    ticker = h['ticker']
                    try:
                        current_price = None
                        if len(tickers_to_check) == 1:
                            current_price = safe_float(prices['Close'].iloc[-1], None)
                        elif ticker in prices.columns.get_level_values(0):
                            current_price = safe_float(prices[ticker]['Close'].iloc[-1], None)

                        if current_price is None:
                            continue

                        spike_close = h.get('close', 0)
                        if spike_close > 0:
                            h['current_price'] = round(current_price, 2)
                            h['return_since_spike'] = round((current_price - spike_close) / spike_close * 100, 1)
                    except Exception:
                        pass
        except Exception:
            pass

    # Save
    with open(history_path, 'w') as f:
        json.dump(history, f, indent=2, default=str)

    print(f"   ✅ History updated: {len(history)} entries (last {HISTORY_DAYS} days)")
    return history


# ─── STEP 4: SAVE TODAY'S DATA ──────────────────────────────────────────────
def save_today(scored_data, history):
    """Save today's scan results as JSON for the dashboard."""
    today_path = os.path.join(DATA_DIR, "today.json")

    # Compute summary stats from history for the dashboard
    returns = [h.get('return_since_spike') for h in history if h.get('return_since_spike') is not None]
    high_score_returns = [h.get('return_since_spike') for h in history
                          if h.get('return_since_spike') is not None and h.get('fade_score', 0) >= 6]

    summary = {
        'scan_date': datetime.now().strftime('%Y-%m-%d'),
        'scan_time': datetime.now().strftime('%H:%M:%S'),
        'total_gainers_found': len(scored_data),
        'high_fade_score_count': len([s for s in scored_data if s['fade_score'] >= 6]),
        'history_total': len(history),
        'history_pct_declined': round(len([r for r in returns if r < 0]) / max(len(returns), 1) * 100, 1) if returns else 0,
        'history_avg_return': round(float(np.mean(returns)), 1) if returns else 0,
        'history_median_return': round(float(np.median(returns)), 1) if returns else 0,
        'high_score_pct_declined': round(len([r for r in high_score_returns if r < 0]) / max(len(high_score_returns), 1) * 100, 1) if high_score_returns else 0,
    }

    output = {
        'summary': summary,
        'candidates': scored_data,
        'recent_history': history[-100:],  # Last 100 entries for the dashboard
    }

    with open(today_path, 'w') as f:
        json.dump(output, f, indent=2, default=str)

    print(f"\n   ✅ Today's data saved to {today_path}")
    return summary


# ─── STEP 5: SEND EMAIL ALERT ───────────────────────────────────────────────
def send_email_alert(scored_data, summary):
    """Send email alert with today's top fade candidates."""
    email_user = os.environ.get('EMAIL_USER', '')
    email_pass = os.environ.get('EMAIL_PASS', '')
    email_to = os.environ.get('EMAIL_TO', email_user)

    if not email_user or not email_pass:
        print("\n   ⚠ Email not configured (set EMAIL_USER, EMAIL_PASS, EMAIL_TO env vars)")
        return

    high_score = [s for s in scored_data if s['fade_score'] >= 6]

    if not high_score and not scored_data:
        print("   📭 No fade candidates today — skipping email")
        return

    print(f"\n📧 Sending email alert to {email_to}...")

    # Build email body
    date_str = datetime.now().strftime('%B %d, %Y')

    html_body = f"""
    <html>
    <body style="font-family: Arial, sans-serif; background: #f5f5f5; padding: 20px;">
    <div style="max-width: 700px; margin: 0 auto; background: white; border-radius: 12px; overflow: hidden;">
        <div style="background: linear-gradient(135deg, #1a1f2e, #0d1117); padding: 25px; text-align: center;">
            <h1 style="color: #f39c12; margin: 0;">Spike Scanner Alert</h1>
            <p style="color: #8899aa; margin: 5px 0 0;">{date_str}</p>
        </div>
        <div style="padding: 25px;">
            <p style="color: #333;">Found <strong>{len(scored_data)} stocks</strong> that spiked 30%+ today.
            <strong style="color: #e74c3c;">{len(high_score)} high-probability fade candidates</strong> (score 6+).</p>
    """

    if high_score:
        html_body += """
            <h3 style="color: #e74c3c; border-bottom: 2px solid #e74c3c; padding-bottom: 8px;">
                Top Fade Candidates (Score 6+)
            </h3>
            <table style="width: 100%; border-collapse: collapse; margin: 15px 0;">
                <tr style="background: #f8f9fa;">
                    <th style="padding: 10px; text-align: left; border-bottom: 2px solid #ddd;">Ticker</th>
                    <th style="padding: 10px; text-align: left; border-bottom: 2px solid #ddd;">Spike</th>
                    <th style="padding: 10px; text-align: left; border-bottom: 2px solid #ddd;">Price</th>
                    <th style="padding: 10px; text-align: left; border-bottom: 2px solid #ddd;">Score</th>
                    <th style="padding: 10px; text-align: left; border-bottom: 2px solid #ddd;">Prob</th>
                    <th style="padding: 10px; text-align: left; border-bottom: 2px solid #ddd;">Why</th>
                </tr>
        """
        for s in high_score[:10]:
            score_color = '#e74c3c' if s['fade_score'] >= 9 else '#f39c12'
            html_body += f"""
                <tr>
                    <td style="padding: 8px; border-bottom: 1px solid #eee;"><strong>{s['ticker']}</strong><br>
                        <small style="color:#888;">{s.get('company_name','')}</small></td>
                    <td style="padding: 8px; border-bottom: 1px solid #eee; color: #27ae60; font-weight: bold;">+{s['daily_gain_pct']}%</td>
                    <td style="padding: 8px; border-bottom: 1px solid #eee;">${s['close']}</td>
                    <td style="padding: 8px; border-bottom: 1px solid #eee; color: {score_color}; font-weight: bold;">{s['fade_score']}/14</td>
                    <td style="padding: 8px; border-bottom: 1px solid #eee;">{s['fade_probability']}%</td>
                    <td style="padding: 8px; border-bottom: 1px solid #eee; font-size: 12px; color: #666;">{', '.join(s['fade_reasons'][:3])}</td>
                </tr>
            """
        html_body += "</table>"

    html_body += f"""
            <div style="background: #f8f9fa; padding: 15px; border-radius: 8px; margin-top: 20px;">
                <p style="margin: 0; color: #666; font-size: 13px;">
                    <strong>Strategy reminder:</strong> Wait 1-3 days after spike for momentum to exhaust.
                    Target 20-30% decline over 2-4 weeks. Stop loss at 15-20% above entry.
                    Position size: 1-2% of portfolio max.
                </p>
            </div>
        </div>
        <div style="background: #f8f9fa; padding: 15px; text-align: center;">
            <p style="color: #999; font-size: 12px; margin: 0;">
                Spike Scanner | Not financial advice | Based on historical pattern analysis
            </p>
        </div>
    </div>
    </body>
    </html>
    """

    msg = MIMEMultipart('alternative')
    msg['Subject'] = f"🔥 Spike Alert: {len(high_score)} fade candidates ({date_str})"
    msg['From'] = email_user
    msg['To'] = email_to
    msg.attach(MIMEText(html_body, 'html'))

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(email_user, email_pass)
            server.send_message(msg)
        print(f"   ✅ Email sent to {email_to}")
    except Exception as e:
        print(f"   ❌ Email failed: {e}")


# ─── MAIN ───────────────────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("  DAILY NASDAQ SPIKE SCANNER")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    os.makedirs(DATA_DIR, exist_ok=True)

    # Step 1: Find today's gainers
    gainers = get_todays_gainers()

    if not gainers:
        print("\n📭 No stocks spiked 30%+ today. Market was calm.")
        # Still save empty data for dashboard but load history first
        history = update_history([])
        save_today([], history)
        return

    # Step 2: Score them
    scored = score_gainers(gainers)

    # Step 3: Update history
    history = update_history(scored)

    # Step 4: Save for dashboard
    summary = save_today(scored, history)

    # Step 5: Email alert
    send_email_alert(scored, summary)

    print("\n" + "=" * 60)
    print("  SCAN COMPLETE!")
    print(f"  Found: {len(scored)} gainers, {len([s for s in scored if s['fade_score'] >= 6])} high-score fade candidates")
    print("=" * 60)


if __name__ == "__main__":
    main()
