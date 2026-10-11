"""Universum laden (results/H1/universe.csv) und Klassen-/Kostenzuordnung."""
import csv, json, os
import numpy as np

COST_CLASS = {"K1": "EQUITY", "K2": "FOREX", "K3": "COMMODITY", "K4": "INDEX", "K5": "CRYPTO"}
SHORT_OK = {"K2", "K3", "K4"}      # Seitenregel EBC-H1: Aktien/ETF/Krypto nur long

def load(path="results/H1/universe.csv"):
    rows = list(csv.DictReader(open(path)))
    for r in rows:
        r["cost_class"] = COST_CLASS[r["klasse"]]
        r["id"] = r["etoro_id"]
    return rows

def sym(r):
    return f"E{r['etoro_id']}.ETORO"
