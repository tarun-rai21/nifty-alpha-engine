import logging
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.company import Company

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Verify against https://www.nseindia.com/products-services/indices-nifty50-index
# if running after June 2026 — composition changes periodically.
NIFTY_50_UNIVERSE = [
    # Financial Services
    {"ticker": "HDFCBANK",   "company_name": "HDFC Bank Ltd",                        "sector": "Financial Services", "industry": "Banking"},
    {"ticker": "ICICIBANK",  "company_name": "ICICI Bank Ltd",                        "sector": "Financial Services", "industry": "Banking"},
    {"ticker": "KOTAKBANK",  "company_name": "Kotak Mahindra Bank Ltd",               "sector": "Financial Services", "industry": "Banking"},
    {"ticker": "AXISBANK",   "company_name": "Axis Bank Ltd",                         "sector": "Financial Services", "industry": "Banking"},
    {"ticker": "SBIN",       "company_name": "State Bank of India",                   "sector": "Financial Services", "industry": "Banking"},
    {"ticker": "INDUSINDBK", "company_name": "IndusInd Bank Ltd",                     "sector": "Financial Services", "industry": "Banking"},
    {"ticker": "BAJFINANCE", "company_name": "Bajaj Finance Ltd",                     "sector": "Financial Services", "industry": "NBFC"},
    {"ticker": "BAJAJFINSV", "company_name": "Bajaj Finserv Ltd",                     "sector": "Financial Services", "industry": "Insurance"},
    {"ticker": "HDFCLIFE",   "company_name": "HDFC Life Insurance Co Ltd",            "sector": "Financial Services", "industry": "Insurance"},
    {"ticker": "SBILIFE",    "company_name": "SBI Life Insurance Co Ltd",             "sector": "Financial Services", "industry": "Insurance"},
    {"ticker": "SHRIRAMFIN", "company_name": "Shriram Finance Ltd",                   "sector": "Financial Services", "industry": "NBFC"},
    # Information Technology
    {"ticker": "TCS",        "company_name": "Tata Consultancy Services Ltd",         "sector": "Information Technology", "industry": "IT Services"},
    {"ticker": "INFY",       "company_name": "Infosys Ltd",                           "sector": "Information Technology", "industry": "IT Services"},
    {"ticker": "HCLTECH",    "company_name": "HCL Technologies Ltd",                  "sector": "Information Technology", "industry": "IT Services"},
    {"ticker": "TECHM",      "company_name": "Tech Mahindra Ltd",                     "sector": "Information Technology", "industry": "IT Services"},
    {"ticker": "LTIM",       "company_name": "LTIMindtree Ltd",                       "sector": "Information Technology", "industry": "IT Services"},
    # FMCG
    {"ticker": "HINDUNILVR", "company_name": "Hindustan Unilever Ltd",                "sector": "FMCG", "industry": "Personal Products"},
    {"ticker": "ITC",        "company_name": "ITC Ltd",                               "sector": "FMCG", "industry": "Cigarettes & Tobacco"},
    {"ticker": "NESTLEIND",  "company_name": "Nestle India Ltd",                      "sector": "FMCG", "industry": "Food Products"},
    {"ticker": "BRITANNIA",  "company_name": "Britannia Industries Ltd",              "sector": "FMCG", "industry": "Food Products"},
    {"ticker": "TATACONSUM", "company_name": "Tata Consumer Products Ltd",            "sector": "FMCG", "industry": "Food Products"},
    # Automobile
    {"ticker": "MARUTI",     "company_name": "Maruti Suzuki India Ltd",               "sector": "Automobile", "industry": "Passenger Vehicles"},
    {"ticker": "TATAMOTORS", "company_name": "Tata Motors Ltd",                       "sector": "Automobile", "industry": "Commercial Vehicles"},
    {"ticker": "M&M",        "company_name": "Mahindra & Mahindra Ltd",               "sector": "Automobile", "industry": "Passenger Vehicles"},
    {"ticker": "BAJAJ-AUTO", "company_name": "Bajaj Auto Ltd",                        "sector": "Automobile", "industry": "Two Wheelers"},
    {"ticker": "HEROMOTOCO", "company_name": "Hero MotoCorp Ltd",                     "sector": "Automobile", "industry": "Two Wheelers"},
    {"ticker": "EICHERMOT",  "company_name": "Eicher Motors Ltd",                     "sector": "Automobile", "industry": "Two Wheelers"},
    # Energy
    {"ticker": "RELIANCE",   "company_name": "Reliance Industries Ltd",               "sector": "Energy", "industry": "Oil & Gas"},
    {"ticker": "ONGC",       "company_name": "Oil & Natural Gas Corporation Ltd",     "sector": "Energy", "industry": "Oil & Gas"},
    {"ticker": "BPCL",       "company_name": "Bharat Petroleum Corporation Ltd",      "sector": "Energy", "industry": "Oil & Gas"},
    {"ticker": "NTPC",       "company_name": "NTPC Ltd",                              "sector": "Energy", "industry": "Power Generation"},
    {"ticker": "POWERGRID",  "company_name": "Power Grid Corporation of India Ltd",   "sector": "Energy", "industry": "Power Transmission"},
    {"ticker": "COALINDIA",  "company_name": "Coal India Ltd",                        "sector": "Energy", "industry": "Mining"},
    # Materials
    {"ticker": "TATASTEEL",  "company_name": "Tata Steel Ltd",                        "sector": "Materials", "industry": "Steel"},
    {"ticker": "JSWSTEEL",   "company_name": "JSW Steel Ltd",                         "sector": "Materials", "industry": "Steel"},
    {"ticker": "HINDALCO",   "company_name": "Hindalco Industries Ltd",               "sector": "Materials", "industry": "Aluminium"},
    {"ticker": "GRASIM",     "company_name": "Grasim Industries Ltd",                 "sector": "Materials", "industry": "Cement"},
    {"ticker": "ULTRACEMCO", "company_name": "UltraTech Cement Ltd",                  "sector": "Materials", "industry": "Cement"},
    # Healthcare
    {"ticker": "SUNPHARMA",  "company_name": "Sun Pharmaceutical Industries Ltd",     "sector": "Healthcare", "industry": "Pharmaceuticals"},
    {"ticker": "DRREDDY",    "company_name": "Dr. Reddy's Laboratories Ltd",          "sector": "Healthcare", "industry": "Pharmaceuticals"},
    {"ticker": "CIPLA",      "company_name": "Cipla Ltd",                             "sector": "Healthcare", "industry": "Pharmaceuticals"},
    {"ticker": "APOLLOHOSP", "company_name": "Apollo Hospitals Enterprise Ltd",       "sector": "Healthcare", "industry": "Hospitals"},
    # Consumer Discretionary
    {"ticker": "TITAN",      "company_name": "Titan Company Ltd",                     "sector": "Consumer Discretionary", "industry": "Jewellery & Watches"},
    {"ticker": "TRENT",      "company_name": "Trent Ltd",                             "sector": "Consumer Discretionary", "industry": "Retail"},
    {"ticker": "ASIANPAINT", "company_name": "Asian Paints Ltd",                      "sector": "Consumer Discretionary", "industry": "Paints"},
    # Industrials
    {"ticker": "LT",         "company_name": "Larsen & Toubro Ltd",                   "sector": "Industrials", "industry": "Construction & Engineering"},
    {"ticker": "ADANIPORTS", "company_name": "Adani Ports & SEZ Ltd",                 "sector": "Industrials", "industry": "Ports"},
    {"ticker": "BEL",        "company_name": "Bharat Electronics Ltd",                "sector": "Industrials", "industry": "Defence Electronics"},
    {"ticker": "ADANIENT",   "company_name": "Adani Enterprises Ltd",                 "sector": "Industrials", "industry": "Diversified"},
    # Telecom
    {"ticker": "BHARTIARTL", "company_name": "Bharti Airtel Ltd",                     "sector": "Telecom", "industry": "Telecom Services"},
]


def seed_nifty50(db: Session) -> None:
    existing = set(db.scalars(select(Company.ticker)).all())
    new = [
        Company(**data, exchange="NSE", is_active=True)
        for data in NIFTY_50_UNIVERSE
        if data["ticker"] not in existing
    ]

    if new:
        db.add_all(new)
        db.commit()
        logger.info("Inserted %d companies.", len(new))
    else:
        logger.info("Universe already seeded. Nothing inserted.")


if __name__ == "__main__":
    with SessionLocal() as db:
        logger.info("Seeding Nifty 50 universe...")
        seed_nifty50(db)
        total = db.scalar(select(func.count()).select_from(Company))
        logger.info("Total companies in DB: %d", total)