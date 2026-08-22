"""
New York Department of State — Open Data Registry Package

Datasets used:
  n9v6-gdp6  Active Corporations Beginning 1800  (primary search)
  63wc-4exh  All Filings                          (filing history enrichment)
  2tms-hftb  Addresses                            (address enrichment)
  kiwr-v7e8  Stock                                (stock info enrichment)
  3gg2-jgnp  Entity                               (entity enrichment)
"""

from .new_york_client import NewYorkCompanyClient
from .new_york_resolver import NewYorkCompanyResolver, NYResolutionResult
from .mapper import NewYorkMapper

__all__ = [
    "NewYorkCompanyClient",
    "NewYorkCompanyResolver",
    "NYResolutionResult",
    "NewYorkMapper",
]
