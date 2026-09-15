"""Physical constants, numerical limits and input defaults (DESIGN.md §1.6, §3, §4.7, §5.8).

All values are SI unless the name carries a unit suffix (``_mm``, ``_nh``, ``_hz``) per §1.6.
"""

from __future__ import annotations

import math

# ---------------------------------------------------------------------------------------------
# Physical constants (§1.6, CODATA 2018; μ0 uses the exact classical value as required)
# ---------------------------------------------------------------------------------------------
MU0: float = 4.0e-7 * math.pi  #: vacuum permeability [H/m]
EPS0: float = 8.8541878128e-12  #: vacuum permittivity [F/m]
C0: float = 299_792_458.0  #: speed of light in vacuum [m/s]

#: Conductivity of copper used in examples and defaults [S/m]
SIGMA_CU: float = 5.8e7

# ---------------------------------------------------------------------------------------------
# Application identity (§5.8.1, §4.7)
# ---------------------------------------------------------------------------------------------
APP_NAME: str = "SimplePICalculator"
APP_DISPLAY_NAME: str = "Simple PI Calculator"
PROJECT_FORMAT: str = "simple-pi-calculator-project"
PROJECT_SUFFIX: str = ".spical.json"
APPDATA_ENV_VAR: str = "SPICAL_APPDATA_DIR"
MAX_RECENT_FILES: int = 8
QUARANTINE_KEEP: int = 5

# ---------------------------------------------------------------------------------------------
# Sweep (§3.1)
# ---------------------------------------------------------------------------------------------
DEFAULT_F_START_HZ: float = 1.0e5
DEFAULT_F_STOP_HZ: float = 1.0e9
DEFAULT_N_POINTS: int = 400
F_START_MIN_HZ: float = 1.0e3
F_STOP_MAX_HZ: float = 2.0e10
F_STOP_WARN_HZ: float = 3.0e9
N_POINTS_MIN: int = 10
N_POINTS_MAX: int = 5000
MARKER_FREQUENCIES_HZ: tuple[float, float, float] = (1.0e6, 1.0e7, 1.0e8)

# ---------------------------------------------------------------------------------------------
# Numerics (§2.4.5, §3.2, §3.4, §3.6, §3.7)
# ---------------------------------------------------------------------------------------------
TAND_FLOOR: float = 1.0e-6  #: minimum tanδ used inside k² and Y_p
SQUARE_GMD_FACTOR: float = 0.44705  #: self-GMD of a square area / side length
K_SPLIT_FACTOR: float = 4.0
PORT_MODE_FACTOR: float = 3.0
MIN_MODES: int = 16
MAX_MODES_PER_AXIS: int = 1500
MNA_GMIN_S: float = 1.0e-12
RCOND_MIN: float = 1.0e-14
Z_PLOT_FLOOR_OHM: float = 1.0e-15
SCHUR_CHUNK_BYTES: int = 256 * 1024 * 1024
VIA_ZERO_LENGTH_M: float = 1.0e-6  #: h_near below this → W_VIA_ZERO_LENGTH (§2.6.1)

# ---------------------------------------------------------------------------------------------
# Placement (§2.5)
# ---------------------------------------------------------------------------------------------
PLANE_HEIGHT_FACTOR: float = 1.4  #: H = 1.4·D_ref
PAD_MARGIN_FACTOR: float = 0.2  #: PAD at y = 0.2·D_ref
X_MARGIN_FACTOR: float = 0.1  #: m_x = w/2 + 0.1·W

# ---------------------------------------------------------------------------------------------
# Input defaults (§4.7) — stored in mm / nH as in the project file
# ---------------------------------------------------------------------------------------------
DEFAULT_DRILL_DIAMETER_MM: float = 0.2
DEFAULT_ANTIPAD_DIAMETER_MM: float = 0.5
DEFAULT_VIA_PITCH_MM: float = 1.0
DEFAULT_VIAS_PER_PAD: int = 1  #: parallel vias on each decap pad (PWR pad and GND pad)
DEFAULT_PAD_VIA_COUNT: int = 1
DEFAULT_N_PADS: int = 1  #: observation pads per PWR net (PWR list column "Number of PADs")
MAX_N_PADS: int = 10000
DEFAULT_VIA_MODEL: str = "pair"
VIA_MODELS: tuple[str, ...] = ("pair", "goldfarb_pucel", "coax")
DEFAULT_PLATING_THICKNESS_MM: float = 0.025
DEFAULT_VIA_CONDUCTIVITY_S_PER_M: float = SIGMA_CU
DEFAULT_MOUNTING_INDUCTANCE_NH: float = 0.0
DEFAULT_S2P_MODE: str = "series"
DEFAULT_WORKERS: int = 0  #: compute worker threads, 0 = auto = os.cpu_count() (§3.9)
MAX_WORKERS: int = 256
S2P_MODES: tuple[str, ...] = ("series", "shunt")
DEFAULT_Z_UNIT: str = "mohm"
Z_UNITS: tuple[str, ...] = ("ohm", "mohm", "uohm")

# ---------------------------------------------------------------------------------------------
# Decap model file extensions (§4.4)
# ---------------------------------------------------------------------------------------------
SPICE_EXTENSIONS: tuple[str, ...] = (".mod", ".lib", ".sp", ".cir", ".sub", ".inc")
S2P_EXTENSIONS: tuple[str, ...] = (".s2p",)
