"""Global Intelligence Network: acquisition of external information.

Design: docs/INTELLIGENCE_ARCHITECTURE.md.

Read-only. It produces normalised observations and market links; it never places a bet.
Modules marked pure in the architecture document import neither ``infrastructure`` nor
``integrations`` nor any network library.
"""
