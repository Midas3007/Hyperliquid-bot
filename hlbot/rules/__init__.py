"""Protocol v3.0 rules engine: pure, testable functions. No network, no exchange, no clock reads.

Every function takes plain numbers (None = data not available) plus the Protocol config, and returns
values or Check results. The engine (hlbot/engine.py) gathers live data and calls these.
"""
