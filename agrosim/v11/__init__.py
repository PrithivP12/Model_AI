"""Versioned, opt-in agronomic diagnostics. V1 remains the default."""
from .kernel import V11Kernel,development_temperature_response

__all__=['V11Kernel','development_temperature_response']
