"""Memoria compartida con nombre de Windows (``CreateFileMappingW``/``OpenFileMappingW``).

``mmap.mmap(-1, n, tagname=…)`` de Python CREA el objeto si no existe, así que no sirve
para saber si CSP ya publicó un archivo (``Car<N>.v0`` aparece solo cuando Custom AI
acepta el coche). Aquí se separan las dos operaciones:

* :func:`create` — crea (o abre si ya existe) un objeto de ``size`` bytes, como hace la
  documentación de Custom AI con ``CarControls<N>.v0``;
* :func:`open_existing` — abre SOLO si existe (``None`` si no).

El espacio de nombres es el de la sesión (``Local\\``), el mismo que usa CSP ("filename
without Local\\ bit", SDK ``ac.writeMemoryMappedFile``).

:class:`MemoryRegistry` es un doble en memoria con la misma interfaz para las pruebas y para
:mod:`tools.acbench.simac` (nunca toca el sistema).
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from typing import Protocol

FILE_MAP_WRITE = 0x0002
FILE_MAP_READ = 0x0004
PAGE_READWRITE = 0x04
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class Region(Protocol):
    name: str
    size: int

    def read(self, offset: int = 0, size: int | None = None) -> bytes: ...

    def write(self, data: bytes, offset: int = 0) -> None: ...

    def close(self) -> None: ...


class Registry(Protocol):
    def create(self, name: str, size: int, fill: bytes | None = None) -> Region: ...

    def open_existing(self, name: str, size: int, writable: bool = False) -> Region | None: ...


# --- Windows -------------------------------------------------------------------------


class WinRegion:
    def __init__(self, name: str, size: int, handle: int, view: int):
        self.name, self.size, self._h, self._v = name, size, handle, view

    def read(self, offset: int = 0, size: int | None = None) -> bytes:
        n = self.size - offset if size is None else size
        if offset < 0 or offset + n > self.size:
            raise ValueError("lectura fuera de la región")
        return ctypes.string_at(self._v + offset, n)

    def write(self, data: bytes, offset: int = 0) -> None:
        if offset < 0 or offset + len(data) > self.size:
            raise ValueError("escritura fuera de la región")
        ctypes.memmove(self._v + offset, data, len(data))

    def close(self) -> None:
        if self._v:
            _k32().UnmapViewOfFile(ctypes.c_void_p(self._v))
            self._v = 0
        if self._h:
            _k32().CloseHandle(wintypes.HANDLE(self._h))
            self._h = 0

    def __del__(self) -> None:  # pragma: no cover - liberación defensiva
        try:
            self.close()
        except Exception:
            pass


_K32 = None


def _k32():
    global _K32
    if _K32 is None:
        if sys.platform != "win32":
            raise OSError("memoria compartida con nombre: solo Windows")
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateFileMappingW.restype = wintypes.HANDLE
        k.CreateFileMappingW.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                                         wintypes.DWORD, wintypes.LPCWSTR]
        k.OpenFileMappingW.restype = wintypes.HANDLE
        k.OpenFileMappingW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
        k.MapViewOfFile.restype = ctypes.c_void_p
        k.MapViewOfFile.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_size_t]
        k.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        _K32 = k
    return _K32


class WinRegistry:
    """Objetos reales del sistema (sesión actual)."""

    def create(self, name: str, size: int, fill: bytes | None = None) -> WinRegion:
        k = _k32()
        ctypes.set_last_error(0)
        h = k.CreateFileMappingW(INVALID_HANDLE_VALUE, None, PAGE_READWRITE, 0, size, name)
        if not h:
            raise OSError(ctypes.get_last_error(), f"CreateFileMappingW({name})")
        existed = ctypes.get_last_error() == 183  # ERROR_ALREADY_EXISTS
        v = k.MapViewOfFile(h, FILE_MAP_READ | FILE_MAP_WRITE, 0, 0, size)
        if not v:
            err = ctypes.get_last_error()
            k.CloseHandle(h)
            raise OSError(err, f"MapViewOfFile({name})")
        reg = WinRegion(name, size, h, v)
        if fill is not None and not existed:
            reg.write(fill[:size])
        return reg

    def open_existing(self, name: str, size: int, writable: bool = False) -> WinRegion | None:
        k = _k32()
        access = FILE_MAP_READ | (FILE_MAP_WRITE if writable else 0)
        h = k.OpenFileMappingW(access, False, name)
        if not h:
            return None
        v = k.MapViewOfFile(h, access, 0, 0, size)
        if not v:
            k.CloseHandle(h)
            return None
        return WinRegion(name, size, h, v)


# --- doble en memoria --------------------------------------------------------------------


class MemRegion:
    def __init__(self, name: str, buf: bytearray):
        self.name, self.size, self._b = name, len(buf), buf

    def read(self, offset: int = 0, size: int | None = None) -> bytes:
        n = self.size - offset if size is None else size
        if offset < 0 or offset + n > self.size:
            raise ValueError("lectura fuera de la región")
        return bytes(self._b[offset: offset + n])

    def write(self, data: bytes, offset: int = 0) -> None:
        if offset < 0 or offset + len(data) > self.size:
            raise ValueError("escritura fuera de la región")
        self._b[offset: offset + len(data)] = data

    def close(self) -> None:
        pass


class MemoryRegistry:
    """Doble de :class:`WinRegistry` para pruebas (un diccionario de búferes)."""

    def __init__(self) -> None:
        self.buffers: dict[str, bytearray] = {}

    def create(self, name: str, size: int, fill: bytes | None = None) -> MemRegion:
        if name not in self.buffers:
            b = bytearray(size)
            if fill is not None:
                b[: len(fill[:size])] = fill[:size]
            self.buffers[name] = b
        return MemRegion(name, self.buffers[name])

    def open_existing(self, name: str, size: int, writable: bool = False) -> MemRegion | None:
        b = self.buffers.get(name)
        if b is None or len(b) < size:
            return None
        return MemRegion(name, b)

    def remove(self, name: str) -> None:
        self.buffers.pop(name, None)
