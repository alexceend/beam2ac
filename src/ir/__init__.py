"""Modelo intermedio del vehículo (IR), independiente del origen y del destino."""

from .param import IRError, Param
from .vehicle import VehicleIR

__all__ = ["IRError", "Param", "VehicleIR"]
