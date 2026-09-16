# Simple PI Calculator — Design Document

| Item | Value |
|---|---|
| Document version | 1.5 — **decap distance distribution** (§2.5.5): `fixed` (default, 0.2.0 behaviour) or `normal` (per-via-set distances from a normal distribution truncated to ±1σ, seeded, reproducible); schema 4 `decaps.distance_mode`/`sigma_mm`/`seed` (§4.7, §5.8.4), cavity cache key and grouping note (§3.9), Decaps tab controls and preview tooltip (§5.5), `I_DIST_SAMPLED` and export headers (§4.8), Appendix D #15. 1.4.1 — review v0.2 fixes (docs/REVIEW-v0.2.md): decap model cache keyed by file content, rounding tolerance of the clipping/overlap warnings, `E_PWR_FAILED`, `W_EXPORT_SKIPPED`. 1.4 — **several observation pads per PWR net**: PWR list column `Number of PADs` N_pad (pad row at y = 0.2·D_ref, one via set of `pad_via_count` pairs per pad, pads joined at an ideal common node; schema 3, §2.5.1, §2.8, §4.3, §4.7). 1.3 — `vias_per_pad` (parallel vias on each decap pad) replaces `vias_per_decap`, schema 2 (§2.6.4, §4.7). 1.2 — physics review incorporated (via-pair loop inductance with via pitch, via length to plane surface, via-cluster port widths, robustness fixes; see Appendix C). 1.1: axial geometry, Top-side only, Dummy Cap, auto-save (baseline for v0.1.0) |
| Status | Implementation-ready |
| License of project | MIT |
| Target platform | Windows 10/11 x64 (development also works on Linux/macOS) |
| Runtime | Python 3.11, PySide6 (Qt 6), pyqtgraph, numpy, openpyxl (scipy permitted, not required by core) |

Implementation agents code **strictly** from this document. Where this document says "MUST", it is a
requirement; "SHOULD" is a strong recommendation; "MAY" is optional. All physical models are
referenced in §10 (tags such as **[Okoshi85]**). No code from other repositories may be copied; the
references are for equations and physical reasoning only.

---

## Table of contents

1. Overview & scope
2. Physics models
3. Numerics
4. Data formats
5. Software architecture
6. Help system
7. Packaging & CI
8. Test plan
9. Limitations
10. References

---

## 1. Overview & scope

### 1.1 Purpose

Simple PI Calculator computes and plots the power-distribution-network (PDN) impedance magnitude
|Z(f)| seen at an IC **PAD** for each power net (PWR) of a PCB or multilayer organic (MLO) substrate.
It is a fast, first-order, frequency-domain "what-if" tool for early decoupling-capacitor planning,
not a full-wave field solver.

### 1.2 Modelled PDN chain

```
 Decap model ── decap via loop ── PWR/GND plane pair (cavity) ── PAD via loop ── PAD (observation port)
 (.mod / .s2p)   (L_via, R_via)     (modal Z-matrix, P ports)      (L_via, R_via)
```

* One PWR plane referenced to one explicit GND plane, dielectric between them derived from the
  stack-up. The plane is rectangular, W × H: the width W is entered by the user, the height H is
  **derived** from the decap distances (H = 1.4·D_ref, §2.5).
* Decaps and the PADs are mounted on the **Top** side (fixed in v1).
* N_pad ≥ 1 PAD (observation/contact pad) ports per PWR net (PWR list column `Number of PADs`,
  default 1) in a row across the width near one end of the plane (y = 0.2·D_ref; a single PAD is
  centred in x). Each pad has its own PAD via set; the pads are joined at an ideal common node on
  the die/probe side.
* For each decap row assigned to the PWR: N identical decaps in a row across the plane width at
  distance d from the PAD along y.
* Each decap via set (n_pad PWR vias on the decap's PWR pad + n_pad GND vias on its GND pad, i.e.
  n_pad PWR/GND via pairs; default n_pad = 1) is one cavity port, loaded by
  the decap impedance (one capacitor, or two in parallel for **Dummy Cap** rows, §2.6.5) in series
  with the via impedance.
* Result Z_PAD(f) = impedance at the common node of the N_pad pads: each pad port's reduced cavity
  impedance in series with its PAD via impedance, all pads driven in parallel (§2.8); for N_pad = 1
  simply the reduced cavity impedance at the PAD port + PAD via impedance.

### 1.3 Inputs (summary)

| # | Input | Source |
|---|---|---|
| 1 | Stack-up | .xlsx (fuzzy headers) |
| 2 | Common via settings (drill diameter, anti-pad diameter, **via pitch** = PWR–GND via centre spacing (default 1.0 mm), **vias per decap pad** n_pad (parallel vias on each of the two decap pads, default 1), **PAD vias (per observation pad)** = PWR/GND via pairs of **each** PAD (default 1); mounting side fixed to Top) | GUI form |
| 3 | PWR list (name, PWR layer, GND layer, plane width, **Number of PADs** N_pad ≥ 1, default 1) | .xlsx import or GUI table |
| 4 | Decap assignment list (incl. optional Dummy Cap flag) | .xlsx import or GUI table |
| 5 | Decap models | SPICE `.mod` subcircuits (required format), Touchstone v1 `.s2p` (optional) |
| 6 | Sweep settings | GUI form (default 100 kHz–1 GHz, 400 log points) |

All inputs and file paths can be saved in a named project file `*.spical.json`. In addition, the
complete application state is **auto-saved continuously** to a per-user file and restored at the next
start (§5.8).

### 1.4 Outputs

* One plot per PWR: log f vs log |Z|, unit Ω / mΩ (default) / µΩ, zoom/pan, vertical markers at
  1 MHz, 10 MHz, 100 MHz with |Z| readouts; optional "plane only (no decaps)" curve.
* Readout table (PWR × marker frequency).
* Export: CSV / XLSX of f, Re Z, Im Z, |Z| per PWR; PNG of plot.

### 1.5 Out of scope for v1

VRM model, bulk capacitors below the plane (other than as ordinary decap rows), IC die/package
model, Bottom-side mounting, user-defined plane height or component coordinates, irregular plane
shapes, plane cut-outs, multiple GND references, via-to-via mutual inductance, time-domain analysis,
target-impedance optimisation. See §9.

### 1.6 Conventions

* Internal units are SI everywhere in `core` (m, s, Hz, H, F, Ω, S/m). Conversions from mm happen
  only in `io` / `gui` layers. Variable names carrying non-SI values MUST have a unit suffix
  (`_mm`, `_nh`, `_mohm`).
* Time-harmonic convention e^{+jωt}; j = √−1; ω = 2πf.
* Physical constants (CODATA 2018): μ0 = 4π·10⁻⁷ H/m (use this exact value; difference to CODATA is
  negligible), ε0 = 8.8541878128·10⁻¹² F/m, c0 = 299 792 458 m/s.
* Layer 1 is the top of the stack-up; z increases downward, z = 0 at the top surface.

---

## 2. Physics models

### 2.1 Stack-up and layer geometry

Each stack-up row i (sorted by Layer Number) has thickness t_i [m], and is either:

* **Metal** — conductivity σ_i > 0 [S/m] present;
* **Dielectric** — conductivity empty or 0; relative permittivity εr_i (Dk) [–], loss tangent
  tanδ_i (Df) [–].

Layer z coordinates [m]:

```
z_top(i)    = Σ_{j<i} t_j
z_bot(i)    = z_top(i) + t_i
z_c(i)      = z_top(i) + t_i/2        (centre of layer i)
T_total     = Σ_j t_j
```

### 2.2 Dielectric between the PWR and GND planes

Let p = PWR layer number, g = GND layer number, lo = min(p,g), hi = max(p,g). The intermediate
layers are I = {i : lo < i < hi}.

**Cavity thickness** [m]:

```
d = Σ_{i∈I} t_i
```

**Equivalent complex permittivity** (series capacitors: the layers are stacked in the field
direction, so the per-area impedances add, d/ε̃_eff = Σ t_i/ε̃_i) **[Ramo94], [Pozar12]**. With the
complex relative permittivity ε̃_i = εr_i·(1 − j tanδ_i):

```
ε̃_eff    = d_d / Σ_{i∈I_d} ( t_i / (εr_i (1 − j tanδ_i)) )     (I_d: dielectric layers in I, d_d = Σ_{I_d} t_i)
εr_eff   = Re ε̃_eff
tanδ_eff = −Im ε̃_eff / Re ε̃_eff
```

This exact form costs nothing and is used by the implementation. To first order in tanδ it reduces to
εr_eff = d_d / Σ t_i/εr_i and tanδ_eff = Σ (t_i/εr_i)·tanδ_i / Σ (t_i/εr_i) (weight t_i/εr_i), and
further to the plain thickness-weighted tanδ when all εr_i are equal (the usual case; then both
forms are identical). The first-order and exact forms differ by < 0.1 % for realistic stacks.

**Intermediate metal layers** (a metal row in I): emit warning `W_STACK_METAL_BETWEEN`
("Metal layer k lies between PWR layer p and GND layer g; it is assumed to be fully cleared (filled
with dielectric) in the plane area"). Its thickness is included in d (geometric gap), and the
cleared region is assumed to have the series-equivalent material of the dielectric layers, i.e.
ε̃_eff as above (sums over dielectric layers only, normalised by d_d), while d = Σ_{I} t_i uses all
intermediate layers. Without intermediate metal d = d_d. If I contains no dielectric layer → error `E_STACK_NO_DIELECTRIC`.

**Worked example** (two prepregs): t = 0.05 mm, εr = 4.0, tanδ = 0.020 and t = 0.05 mm, εr = 3.5,
tanδ = 0.010 → d = 0.1 mm; exact: εr_eff = 3.733426, tanδ_eff = 0.01466592 (first order: 3.733333,
0.01466667). Extreme mix (0.05 mm εr 3.0 tanδ 0.002 + 0.05 mm εr 4.5 tanδ 0.03): exact
εr_eff = 3.600677, tanδ_eff = 0.01319398.

### 2.3 Plane capacitance (DC limit)

```
C_plane = ε0 εr_eff a b / d          [F]    (a, b [m]; d [m])
```

**Sanity example:** a = b = 20 mm, d = 0.1 mm, εr = 4:
C_plane = 8.8541878128e-12 × 4 × (0.02 × 0.02) / 1e-4 = **141.667 pF**.
At 1 MHz, |1/(ωC)| = **1123.44 Ω**.

Second example (bundled example project, VDD_CORE with derived height, §2.5): a = W = 60 mm,
b = H = 21 mm, d = 0.1 mm, εr = 4.3 → C_plane = **479.72 pF**.

### 2.4 Cavity resonator (planar circuit) model

#### 2.4.1 Physical basis

The parallel-plate pair with d ≪ wavelength and d ≪ a, b supports only the TM_z modes with no
z-variation; with open (magnetic-wall) edges the voltage between the planes satisfies the 2-D
Helmholtz equation with Neumann boundary conditions **[Okoshi85], [Lei99], [Swaminathan07],
[Novak07]**. Define the per-square series impedance Z_p and shunt admittance Y_p of the plane pair
(telegrapher form of the planar circuit **[Kim01], [Swaminathan07]**):

```
Z_p(ω) = jωμ0 d + Z_s,PWR(ω) + Z_s,GND(ω)         [Ω per square]
Y_p(ω) = jωε0 εr_eff (1 − j tanδ_eff) / d          [S per square]
∇_t V = −Z_p J_s ,   ∇_t·J_s = −Y_p V + J_z  ⇒   ∇_t² V + k² V = −Z_p J_z
k²(ω)  = −Z_p Y_p
```

Writing Z_p = jωμ0 d · Γ_c with the **conductor-loss factor**

```
Γ_c(ω) = 1 + (Z_s,PWR(ω) + Z_s,GND(ω)) / (jωμ0 d)
```

gives the lossy wavenumber

```
k²(ω) = ω² μ0 ε0 εr_eff · (1 − j tanδ_eff) · Γ_c(ω)          [rad²/m²]
```

For thick conductors (t ≫ δ), Z_s = (1+j)/(σδ), and Γ_c = 1 + (1−j)(δ_P+δ_G)/(2d); for identical
planes this is 1 + (1−j)δ/d, i.e. k = ω√(με)·√((1 − j tanδ)(1 + (1−j)δ/d)) ≈ ω√(με)·√(1 − j(tanδ + δ/d))
to first order, which is the well-known lossy-cavity form of **[Lei99]** (r = δ = skin depth; our
form additionally keeps the real part δ/d, the internal inductance of the plane metal).

**Design decision (important):** the modal-sum prefactor uses Z_p = jωμ0 d Γ_c (not jωμ0 d alone).
This keeps the (0,0) term exactly capacitive at low frequency even with conductor loss (see
§2.4.4). Using the bare jωμ0 d prefactor with a lossy k would give a spurious finite resistance at
DC because Γ_c → ∞ as ω → 0 for finite σ·t.

#### 2.4.2 Surface impedance of a finite-thickness plane

For a plane of conductivity σ [S/m] and thickness t [m] (finite-thickness conductor surface impedance, **[Ramo94]**; skin depth also **[Wheeler42]**):

```
δ(ω)   = sqrt(2 / (ω μ0 σ))               [m]    skin depth
γ      = (1 + j) / δ                      [1/m]
η_c    = (1 + j) / (σ δ)                  [Ω]
Z_s(ω) = η_c · coth(γ t)                  [Ω per square]
```

Limits: t ≫ δ → Z_s → (1+j)/(σδ); t ≪ δ → Z_s → 1/(σt) + jωμ0 t/3 (DC sheet resistance plus internal
inductance). σ = +∞ (ideal conductor, allowed in tests via `math.inf`) → Z_s = 0.

Numerically: coth(z) = (1 + e^{−2z})/(1 − e^{−2z}) for |z| ≥ 1e-3; for |z| < 1e-3 use
coth(z) ≈ 1/z + z/3. (Re z > 0 always, so e^{−2z} never overflows.)

Skin depth of copper (σ = 5.8e7 S/m): δ(1 MHz) = 66.09 µm, δ(100 MHz) = 6.609 µm.

#### 2.4.3 Port impedance matrix

Plane occupies 0 ≤ x ≤ a, 0 ≤ y ≤ b. Ports i = 0…P−1 are small squares of side w_i centred at
(x_i, y_i) (w_i from §2.4.5). Uniform current density over each port area, voltage averaged over the port area
**[Okoshi85], [Lei99]**:

```
Z_ij(ω) = (Z_p(ω) / (a b)) · Σ_{m=0}^{M} Σ_{n=0}^{N}  C_m C_n
          · cos(mπx_i/a) cos(nπy_i/b) cos(mπx_j/a) cos(nπy_j/b)
          · S_{m,i} S_{n,i} S_{m,j} S_{n,j}  /  (k_mn² − k²(ω))                  [Ω]

C_m     = 1 for m = 0,  2 for m ≥ 1          (C_n likewise)
S_{m,i} = sinc_u(mπ w_i / (2a)),  S_{n,i} = sinc_u(nπ w_i / (2b)),   sinc_u(u) = sin(u)/u, sinc_u(0) = 1
k_mn² = (mπ/a)² + (nπ/b)²                    [rad²/m²]
Z_p   = jωμ0 d Γ_c(ω)
```

Notes:

* C_m C_n equals the χ_mn² factor of **[Lei99]** (χ = 1, √2, 2).
* The sinc factor comes from averaging cos(mπx/a) over [x_i − w_i/2, x_i + w_i/2]:
  (1/w_i)∫cos(mπx/a)dx = cos(mπx_i/a)·sinc_u(mπw_i/(2a)). With `numpy.sinc` (normalised,
  sin(πx)/(πx)) this is `np.sinc(m*w_i/(2*a))`. Implementers MUST use this exact argument.
* Port widths may differ per port: the PAD port and the decap ports get their own widths from their
  via-cluster size (§2.4.5).
* Z is complex symmetric (Z_ij = Z_ji), **not** Hermitian.

**Normalisation check (DC limit).** Only (m,n) = (0,0) has k_mn = 0. Its term is

```
Z_ij^(00) = Z_p/(ab) · 1/(−k²) = Z_p/(ab) · 1/(Z_p Y_p) = 1/(ab Y_p)
          = d / (jω ε0 εr_eff (1 − j tanδ_eff) a b) = 1 / (jω C_plane (1 − j tanδ_eff))
```

independent of port positions and of Γ_c. All other terms are proportional to Z_p(ω): for σ = ∞
they vanish ∝ ω (inductive); for finite σ they tend to the real DC spreading resistance of the plane
metal (Z_p → 1/(σ_P t_P) + 1/(σ_G t_G) per square). Both are negligible against 1/(ωC_plane) at low
frequency. Hence Z_ij → 1/(jωC_plane) as ω → 0, confirming the normalisation. (Verified numerically
at design time and by the independent review: a = b = 20 mm, d = 0.1 mm, εr = 4, tanδ = 0, σ = ∞,
f = 1 kHz → |Z_00·jωC_plane − 1| = 1.0e-6, which is exactly the tanδ floor of §3.4; the imaginary
part agrees to 1e-12. With Cu 35 µm planes the residual real part is 0.6248 mΩ.)

#### 2.4.4 Plane resonances

Lossless resonance frequencies:

```
f_mn = c0 / (2 √εr_eff) · sqrt((m/a)² + (n/b)²)          [Hz]
```

Examples: a = b = 20 mm, εr = 4: f_10 = 3.7474 GHz, f_11 = 5.2996 GHz.
a = 100 mm, b = 80 mm, εr = 4: f_10 = 749.48 MHz, f_01 = 936.85 MHz (both confirmed as |Z_00|
peaks of the prototype at a corner port).
a = 60 mm, b = 21 mm, εr = 4.3 (VDD_CORE example): f_10 = 1.2048 GHz, f_01 = 3.4422 GHz.

#### 2.4.5 Equivalent port size of a via

A via barrel of radius r0 = D_drill/2 carries current on a circle. In the static (inductive) limit
the self term of the modal sum equals the double area-average of the 2-D Green's function over the
square port, i.e. a logarithm of the **self geometric mean distance (GMD)** of the square area,
which is 0.44705·w **[Grover46], [Paul10]**. The GMD of a circular ring (the barrel) with itself
is its radius r0. Equating them:

```
w = r0 / 0.44705 = 2.2369 · r0 = 1.11845 · D_drill          [m]
```

Design-time verification: ideal conductors, 100 mm × 100 mm, d = 0.1 mm, two vias of 0.5 mm drill
10 mm apart at (50,50) and (60,50) mm: loop inductance Im(Z_00+Z_11−2Z_01)/ω at 1 MHz = 0.1482 nH
versus the image-theory result (μ0 d/π)·ln(s/r0) = 0.1476 nH (0.4 % error). A square of equal
"conformal radius" (w = r0/0.5902) would give 0.1593 nH (+8 %), so the GMD rule is chosen.

The anti-pad diameter does **not** enter the port size (it governs only the short segment through
the nearer plane, §2.6.2).

**Via-cluster ports (several via pairs on one port).** Each cavity port is formed by the n_p vias
that cross the cavity (one per PWR/GND via pair: the PWR via if the GND plane is nearer, otherwise
the GND via). For n_p > 1 the same GMD argument is extended to n conductors **[Grover46], [Paul10]**:

```
ln g_p = (1/n_p²) · Σ_{i=1..n_p} Σ_{j=1..n_p} ln d_ij ,     d_ii = r0 ,  d_ij = centre distance
w_p    = g_p / 0.44705                                                       [m]
```

Cluster arrangement (normative): the n_p cavity-crossing vias lie on a square grid of pitch
p_c = √2·s_v (s_v = via pitch, §2.6.2; a checkerboard of alternating PWR and GND vias with nearest
PWR–GND spacing s_v has same-net pitch √2·s_v), filled row-major with cols = ceil(√n_p) columns
(via i at column i mod cols, row i div cols). n_p = pad_via_count for every PAD port (each of the
N_pad pads, §2.5.1) and
n_p = n_pad = `vias_per_pad` for every decap port (a decap via set of n_pad PWR + n_pad GND vias has
n_pad cavity-crossing vias). For n_p = 1, g_p = r0 and w_p = 2.2369·r0.

Values for D_drill = 0.2 mm, s_v = 1.0 mm (p_c = 1.41421 mm): n = 1 → w = 0.22369 mm; n = 2 →
0.84120 mm; n = 4 → 1.77893 mm; n = 9 → 3.45132 mm.

Design-time verification (VDD_IO plane 30 × 14 mm, d = 0.1 mm, εr = 4, σ = ∞, 1 MHz; 4 PAD vias in
the cluster above centred at (15, 2) mm; a single decap via at (15, 12) mm shorted): explicit 4
separate ports in parallel 0.16653 nH; one cluster port w = 1.77893 mm 0.16760 nH (+0.6 %); one
single-via port (old rule) 0.20901 nH (+25 %).

### 2.5 Plane size and port placement geometry

All components (decaps and PADs) are on the **Top** side. The modelled plane is a rectangle
0 ≤ x ≤ W (width, user input), 0 ≤ y ≤ H (height, derived). Throughout this section a ≡ W and
b ≡ H in the cavity formulas of §2.4; w_pad is the width of every PAD port and w (≡ w_dec) the common decap
port width, both from §2.4.5 (for the defaults, 1 PAD via pair and 1 via per decap pad, both equal
2.2369·r0). Because w enters the x margin m_x, the usable span L_x, the sub-row split and the sub-row
pitch (§2.5.3) and the overlap/clipping checks, the vias per decap pad also change the placement.

#### 2.5.1 Derived plane height and PAD row

Let the enabled decap rows assigned to the PWR be k = 0 … G−1 (table order), with distance to PAD
d_k > 0 [m], count N_k ≥ 1, and Dummy Cap flag δ_k ∈ {false, true}. The PWR net has N_pad ≥ 1
observation pads (PWR list column `Number of PADs`, integer, default 1, §4.3).

```
D_ref = max_k d_k                      if G ≥ 1          [m]   (normal distance mode: max_k d_k + σ, §2.5.5)
D_ref = W / 1.4                        if G = 0 (no decap rows: square plane H = W, plane-only result)
H     = 1.4 · D_ref                                      [m]
y_0   = 0.2 · D_ref                                      [m]  PAD-row centre line
```

**PAD row.** The N_pad pads are placed with the decap-row rule of §2.5.3, applied to a row of
N_pad ports of width w_pad centred on y_0:

```
N_pad = 1:  PAD 0 : (x_0, y_0) = (W/2, 0.2 · D_ref)                    (the v1.3 single PAD, exactly)
N_pad ≥ 2:
    m_p    = w_pad/2 + 0.1 · W                         x margin
    L_p    = W − 2 · m_p                               usable span
    n_rowp = N_pad                          if L_p / N_pad ≥ w_pad
    n_rowp = max(1, floor(L_p / w_pad))     otherwise
    R_p    = ceil(N_pad / n_rowp)                      number of PAD sub-rows
    for r = 0 … R_p−1:
        n_r = min(n_rowp, N_pad − r·n_rowp)
        y_r = y_0 + (r − (R_p − 1)/2) · w_pad
        for i = 0 … n_r−1:
            x_{PAD,r,i} = m_p + (i + 0.5) · L_p / n_r
            y_{PAD,r,i} = clip(y_r, w_pad/2, H − w_pad/2)
```

(The general formula also gives x = W/2 for N_pad = 1; the single PAD is written as W/2 so that the
v1.3 coordinates, and therefore all §8 golden values, are reproduced bit-identically.) Each pad is
one cavity port of width w_pad = w_p(n_p = pad_via_count) (§2.4.5) with its own via set of
`pad_via_count` PWR/GND via pairs (§2.6.4): the total number of PAD via pairs is
N_pad · pad_via_count.

So the PAD row sits at a 20 % (of D_ref) margin from the y = 0 edge, the farthest decap row sits at
y = 1.2·D_ref, leaving the same 20 % margin to the y = H edge. The PADs and the decaps are at
opposite ends of the plane along y. **`Distance to PAD` d_k is measured from the PAD-row centre line
y_0 to the decap-row centre line** for any N_pad (unchanged).

Validation:

* d_k ≤ 0 → error `E_DECAP_DISTANCE`.
* N_pad not an integer ≥ 1 → error `E_PWR_NPADS`.
* 0.2·D_ref < w_pad/2 (PAD row would not fit inside the plane; with a single PAD via D_ref < 2.5·w,
  i.e. < 0.56 mm for a 0.2 mm drill) → error `E_DREF_TOO_SMALL`. Also error `E_PWR_WIDTH_TOO_SMALL`
  if w_pad > W, or if N_pad ≥ 2 and L_p < w_pad (W < 2.5·w_pad).
* A PAD sub-row clipped to the plane edge → warning `W_PAD_CLIPPED` (number of clipped pads).
* d_k < (w + w_pad)/2 → warning `W_DECAP_TOO_CLOSE` (the decap port overlaps the PAD port footprint in y).

#### 2.5.2 Ports per decap row

```
P_k = N_k                 if δ_k = false
P_k = ceil(N_k / 2)       if δ_k = true           (number of via sets = cavity ports of row k)
```

The number of capacitors loading each port of row k is defined in §2.6.5.

#### 2.5.3 Port coordinates of row k

```
y_k   = 0.2 · D_ref + d_k                                  row centre line
m_x   = w/2 + 0.1 · W                                      x margin (port half-size + 10 % of W)
L_x   = W − 2 · m_x                                        usable span
```

Validation: L_x < w (L_x = 0.8·W − w, so W < 2.5·w) → error `E_PWR_WIDTH_TOO_SMALL`.

Single row if the cell pitch is at least one port size, otherwise several rows at a pitch of w,
symmetric about y_k:

```
n_row = P_k                              if L_x / P_k ≥ w
n_row = max(1, floor(L_x / w))           otherwise
R_k   = ceil(P_k / n_row)                number of sub-rows
for r = 0 … R_k−1:
    n_r   = min(n_row, P_k − r·n_row)                        (only the last sub-row may be shorter)
    y_r   = y_k + (r − (R_k − 1)/2) · w
    for i = 0 … n_r−1:
        x_{k,r,i} = m_x + (i + 0.5) · L_x / n_r               (cell-centred, even spacing L_x/n_r ≥ w)
        y_{k,r,i} = clip(y_r, w/2, H − w/2)
```

* P_k = 1 gives x = W/2 (centred), directly "above" a single PAD.
* With the `normal` distance distribution (§2.5.5) the y of every decap port is shifted by its own
  d_kj − d_k after the sub-row rule (x, sub-row index and port order unchanged).
* **Port order** (normative, defines port indices): PADs = ports 0 … N_pad−1 (PAD sub-rows r
  ascending, within a sub-row left to right); then rows k in table order; within a row, sub-rows r
  ascending; within a sub-row, i ascending (left to right). The array `group_index[p − N_pad]` maps
  each decap port p to k.
* If any y was clipped → warning `W_DECAP_CLIPPED` (row k, number of clipped ports).
* If two ports i, j (any rows, including the PADs) have |Δx| < (w_i + w_j)/2 **and**
  |Δy| < (w_i + w_j)/2 (overlapping square footprints) → warning `W_PORT_OVERLAP` (e.g. two rows with almost equal distances). The computation
  still proceeds (load impedances keep the reduced matrix non-singular).

**Worked example (bundled VDD_CORE, D_drill = 0.2 mm ⇒ w = 0.22369 mm):** W = 60 mm, rows
(10 × 0402 at 8 mm, 4 × 10 µF at 15 mm) → D_ref = 15 mm, H = 21 mm, PAD (30, 3) mm;
m_x = 6.11185 mm, L_x = 47.7763 mm; row 0: y = 11 mm, x = 8.5007 + 4.7776·i mm (i = 0…9);
row 1: y = 18 mm, x = 12.0839, 24.0280, 35.9720, 47.9161 mm.

#### 2.5.4 Approximation statement (to be repeated in Help)

* The real plane outline and component coordinates are **not** input. The tool synthesises a
  rectangular plane of the real width W and a height that just fits the PAD and all decap rows with
  20 % end margins. `Distance to PAD` is interpreted as the separation **along y** between the PAD
  row and the decap row; decaps spread across the width are therefore farther from a single PAD
  (√((x − W/2)² + d_k²)) than d_k, which mimics a row of capacitors along a board edge or a
  capacitor bank opposite the IC. Several PADs (N_pad ≥ 2) are likewise spread evenly across the
  width (a row of IC power pins/bumps along the die edge), not clustered at the real pin-field
  positions.
* Because the modelled plane area is W × 1.4·D_ref, the plane capacitance and plane resonance
  frequencies are those of this synthetic plane, not of the real plane. For typical designs the
  decap capacitance dominates below the first resonance, but the plane-only curve and the resonance
  positions must be read as indicative.
* The spreading inductance between PAD and decaps (logarithmic in distance, §2.4.5) and the
  parallelism of the decap paths, which dominate the mid-frequency impedance, are approximately
  preserved; because the PAD lies only 0.2·D_ref from the synthetic plane edge, the edge image
  (magnetic wall) increases the PAD–decap spreading inductance by typically 10–15 % relative to a
  large plane (pessimistic). Review evidence (VDD_IO geometry, 10 mm separation, σ = ∞, 1 MHz):
  30 × 14 mm 0.2090 nH, 60 × 60 mm 0.1860 nH, infinite-plane image result 0.1842 nH.
* A lumped "spreading inductance" fallback is not provided.

#### 2.5.5 Distance distribution (fixed / truncated normal)

A global option (project key `decaps.distance_mode`, §4.7; GUI Decaps tab, §5.5) applies to **every**
enabled decap row of every PWR net.

* **`fixed`** (default): every port of row k is at d_k (§2.5.3). This is exactly the 0.2.0 / schema-3
  behaviour; code path, cavity cache keys and results are bit-identical.
* **`normal`**: each cavity port (via set) j = 0 … P_k−1 of row k gets its own distance

  ```
  d_kj = d_k + σ · z_kj,        z_kj ~ TN(0, 1; −1, 1)          σ > 0 absolute [m] (`sigma_mm`, default 0.5 mm)
  ```

  where TN(0, 1; a, b) is the standard normal distribution truncated to [a, b] = [−1, 1] with density
  f(z) = φ(z) / (Φ(b) − Φ(a)) for a ≤ z ≤ b and 0 otherwise, φ(z) = e^(−z²/2)/√(2π),
  Φ(z) = ½·erfc(−z/√2) **[JKB94]**. Hence d_k − σ ≤ d_kj ≤ d_k + σ; E[z] = 0 and
  Var[z] = 1 − 2φ(1)/(Φ(1) − Φ(−1)) = 0.29113 (standard deviation 0.5396·σ). σ is therefore the
  half-width of the admissible band and the scale of the parent normal, not the sample spread.

**Sampling (normative, reproducible).** Inverse-transform sampling of the truncated normal:

```
rng  = numpy.random.default_rng(seed)             seed: int 0 … 2³¹−1 (`decaps.seed`, default 12345), PCG64
u    = rng.random(n)                              uniform [0, 1), float64
p    = Φ(−1) + u · (Φ(1) − Φ(−1))
z    = Φ⁻¹(p), clipped to [−1, 1]                 (guards the last-ulp rounding only)
```

Φ uses `math.erfc` (vectorised element-wise); Φ⁻¹ is Acklam's rational approximation
(relative error < 1.15e-9) refined by one Halley step
x ← x − u/(1 + x·u/2), u = (Φ(x) − p)·√(2π)·e^(x²/2) **[Acklam03]**, giving |Φ(Φ⁻¹(p)) − p| < 1e-13·p.
For p > 1 − 0.02425 the step is taken on the mirrored lower tail (x → −x, p → 1 − p, exact) because
Φ(x) rounds to 1 there; Φ⁻¹ then agrees with an erfc bisection to < 1e-12 over [−8, 8] (review v0.3).
No scipy. PCG64 and float64 arithmetic are platform independent; results for a given seed are
identical across platforms up to the last-ulp behaviour of the C library `erfc`.

Order: **one generator per computation**, created with the seed, drawn by the **enabled decap rows of the
whole decap table in table order** (all PWR nets, before any net is computed — so the samples do not
depend on the PWR order, worker threads, or failures of other nets), each row drawing P_k values in
**port order** (§2.5.3). A Dummy Cap row (§2.6.5) draws **one sample per via set** (port), not per
capacitor: the two capacitors of a pair share the via set and move together. Rows with N_k < 1 draw
nothing. Consequently enabling/disabling a row or changing a count changes the samples of the rows
below it (documented in Help). The GUI placement preview calls the same function
(`engine.sample_project_distances`), so it shows exactly the computed geometry.

**Geometry.** The x positions and the sub-row pattern of §2.5.3 are kept; port j of row k is moved in
y by d_kj − d_k:

```
D_ref  = max_k d_k + σ                              (G = 0: W/1.4 as before; fixed mode: max_k d_k)
H      = 1.4 · D_ref,     y_0 = 0.2 · D_ref           (the PAD row follows D_ref)
y_kj   = clip(y_r(j) + (d_kj − d_k), w/2, H − w/2)    y_r(j) = sub-row centre line of port j (§2.5.3)
```

D_ref is the upper truncation bound of every sample, so H is **independent of the seed** (only σ and
the row distances enter) and still contains every port with the 20 % margins. (0.3 review F4: an earlier
draft used max_kj d_kj, which made the plane size — and its capacitance and anti-resonances — change with
the seed; on the example VDD_IO net the ≈ 616 MHz peak moved by ±2 %.) Clipping counts per port (`W_DECAP_CLIPPED`),
`W_DECAP_TOO_CLOSE` uses min_j d_kj, `W_PORT_OVERLAP` is unchanged. `Placement.port_distances_m`
holds d_kj (= d_k in fixed mode).

**Validation / messages.** `E_DIST_MODE` (mode not `fixed`/`normal`), `E_DIST_SIGMA` (σ ≤ 0 or not
finite), `E_DIST_SEED` (not an integer 0 … 2³¹−1) are global errors (source `Decaps`, checked only in
normal mode for σ and seed). `W_DIST_SIGMA_LARGE` per row when σ ≥ d_k (samples may reach the PAD row;
negative d_kj are clipped to the plane edge). Info `I_DIST_SAMPLED` per PWR in normal mode: σ, seed,
min/mean/max of the sampled distances and D_ref.

**Results.** `PwrResult.distance` (the `DistanceDistribution`, `None` for fixed) and
`PwrResult.sampled_distances`: list of (row index k — position among the net's enabled rows, as in
`group_index` —, port index j within the row, d_kj in mm) for every decap port, in port order (fixed mode:
d_k). Exports record a summary line (§4.8).

### 2.6 Via model

#### 2.6.1 Geometry of the via loops

Decaps and the PAD are both on the **Top** side (fixed in v1), so the surface coordinate is z_s = 0
for both. Each via pair consists of a PWR via (Top → PWR layer) and a GND return via (Top → GND
layer) at centre spacing s_v. With p = PWR layer, g = GND layer:

```
n       = min(p, g)                 nearer plane (the one closer to the Top surface)
h_near  = z_top(n)                  [m]  Top surface → component-side face of the nearer plane
t_near  = t_n                       [m]  thickness of the nearer plane
h_R     = 2·h_near + t_near         [m]  conductor length used for the via resistance (§2.6.3)
```

Physical picture (above ~5 MHz a 35 µm plane is thicker than the skin depth):

1. From the Top surface down to the component-side face of the nearer plane, the PWR via and the
   GND via form a two-conductor loop of length h_near; its flux is bounded by the nearer plane's
   surface (image plane) **[Paul10], [Ramo94]**.
2. Inside the nearer plane's thickness t_near only the via that continues to the far plane has open
   area, namely its anti-pad annulus (coaxial segment).
3. Below the nearer plane, the dielectric gap d is represented by the cavity port (§2.4.5); the
   plane metal itself is in Γ_c (§2.4.1). This segment MUST NOT be counted again.

The same geometry applies to decap via pairs and PAD via pairs (same side, same PWR/GND pair).
Example stack-up (§4.2): PWR 5 / GND 3 → n = 3, h_near = z_top(3) = 0.135 mm, t_near = 0.035 mm,
h_R = 0.305 mm; PWR 7 / GND 9 → n = 7, h_near = z_top(7) = 1.105 mm, h_R = 2.245 mm.

If h_near < 1 µm (the nearer plane is layer 1, the Top metal itself) set L_pair = 0, use
h_R = t_near, keep L_ap, and emit `W_VIA_ZERO_LENGTH`.

#### 2.6.2 Inductance — chosen model: image partial-inductance via pair

Inputs: r0 = D_drill/2, r_ap = D_antipad/2, via pitch s_v (`vias.via_pitch_mm`, default 1.0 mm).
Validation: s_v ≤ D_drill → error `E_VIA_PITCH`; s_v < (D_drill + D_antipad)/2 (the GND via would
cut into the PWR via's anti-pad) → warning `W_VIA_PITCH_SMALL`; D_antipad ≤ D_drill → error
`E_VIA_ANTIPAD`.

Partial mutual inductance of two parallel filaments of length l at distance x
**[Grover46], [Paul10]**; with x = r0 it is the partial self-inductance of a thin tube:

```
M_p(l, x) = (μ0/2π) · [ l·ln((l + √(l² + x²)) / x) − √(l² + x²) + x ]              [H]
```

Default model `via_model = "pair"`:

```
L_pair(h, s_v) = M_p(2h, r0) − M_p(2h, s_v)          [H]   two barrels of length h ending on the
                                                           nearer plane; image method doubles the
                                                           length and halves the loop (L_p − M_p)
L_ap           = (μ0/2π) · t_near · ln(r_ap / r0)    [H]   coaxial segment through the anti-pad
L_loop         = L_pair(h_near, s_v) + L_ap          [H]   one PWR/GND via pair
```

For h ≫ s_v, L_pair → (μ0 h/π)·ln(s_v/r0), the two-wire / image result of §2.4.5 and the familiar
rule of thumb "≈ 10.16·h[in]·ln(s/r) nH" **[Bogatin18], [Novak07]**.

**Decision and reasons.** Above the nearer plane the return current of the PWR via flows in the GND
via, and the field between the two barrels is tangential to any plane they cross, so the loop
logarithm is ln(s_v/r0), not ln(r_ap/r0). The anti-pad only matters over the thickness of the nearer
plane (L_ap). The image partial-inductance form is used instead of the purely 2-D
(μ0h/π)·acosh(s_v/(2r0)) because it includes the end effect of short barrels (h ≲ s_v), which is the
common case for shallow layer pairs.

Alternatives (advanced option, comparison only):

* `via_model = "goldfarb_pucel"` **[Goldfarb91]**, partial self-inductance of a via post, with the
  mutual term subtracted as required for a loop (L_loop = L_p1 + L_p2 − 2M_12, **[Paul10]**):

  ```
  L_GP(h)  = (μ0/2π) · [ h ln((h + √(r0² + h²)) / r0) + 1.5 (r0 − √(r0² + h²)) ]     [H]
  L_loop   = 2·(L_GP(h_near) − M_p(h_near, s_v)) + L_ap
  ```

* `via_model = "coax"` (legacy, pre-review): L_loop = (μ0/2π)·ln(r_ap/r0)·h_R. Kept only to compare
  with earlier results; it underestimates long via pairs by about 2×.

**Sanity examples** (D_drill = 0.2 mm, D_antipad = 0.5 mm):

| Quantity | Value |
|---|---|
| M_p(1 mm, 1 mm) | 0.093432 nH |
| M_p(1 mm, 0.1 mm) (tube self partial) | 0.418647 nH |
| L_pair(h = 1 mm, s_v = 1 mm) | 0.765061 nH (2-D asymptote (μ0h/π)ln(s/r0) = 0.921034 nH) |
| L_pair(0.1525 mm, 0.5 mm) | 0.049604 nH |
| L_pair(1.1225 mm, 1.0 mm) | 0.875391 nH |
| L_pair(5 mm, 1.0 mm) | 4.430114 nH |
| L_ap(t = 35 µm) | 0.0064140 nH |
| Legacy coax per mm, L_GP(1 mm) | 0.183258 nH, 0.328148 nH |
| GP loop 2(L_GP − M_p)(1.1225 mm, 1 mm) (without L_ap) | 0.549560 nH |

Example stack-up, s_v = 1.0 mm: PWR 5/GND 3 → L_pair = 0.047997 nH, **L_loop = 0.054411 nH**;
PWR 7/GND 9 → L_pair = 0.859598 nH, **L_loop = 0.866012 nH**.

The optional user **mounting inductance** L_mount (pad + trace + solder of one capacitor, advanced
setting, default 0 nH) is attached to each capacitor, not to the via set (§2.6.5).

#### 2.6.3 Resistance with skin effect

Barrel is a plated tube, plating thickness t_pl (advanced, default 0.025 mm; if t_pl ≥ r0 the via is
solid), via conductivity σ_v (advanced, default 5.8e7 S/m). Smooth engineering interpolation of the
current-carrying wall depth (an engineering interpolation between the DC tube and the skin-effect
limit; skin depth per **[Ramo94], [Wheeler42]**):

```
δ_v(ω)  = sqrt(2 / (ω μ0 σ_v))
δ_e(ω)  = t_eff · (1 − exp(−δ_v / t_eff)),     t_eff = min(t_pl, r0)
A_e(ω)  = π (r0² − (r0 − δ_e)²)                [m²]
R'_v(ω) = 1 / (σ_v A_e(ω))                     [Ω/m]
R_loop  = R'_v · h_R                           [Ω]   (h_R from §2.6.1)
```

Limits: low f → δ_e → t_eff (DC tube resistance); high f → δ_e → δ_v (skin-limited).
Example stack-up at 1 MHz: h_R = 0.305 mm → 0.40775 mΩ; h_R = 2.245 mm → 3.00130 mΩ.
Sanity (D_drill 0.2 mm, t_pl 25 µm, 1 mm, Cu): R = 1.254 mΩ @1 kHz, 1.337 mΩ @1 MHz,
4.867 mΩ @100 MHz, 13.83 mΩ @1 GHz.

#### 2.6.4 Via impedance per port

A decap always has two pads (PWR and GND). `vias_per_pad` = n_pad (integer ≥ 1, default 1) is the
number of parallel vias on **each** decap pad, so one decap via set consists of n_pad PWR vias and
n_pad GND vias, arranged as n_pad PWR/GND pairs at pitch s_v. **Each** of the N_pad PADs
(observation pads, §2.5.1) has its own via set of `pad_via_count` = n_pad,PAD PWR/GND via pairs
("PAD vias = vias per observation pad"), so the PAD via count grows in proportion to N_pad. Both
kinds of via sets use the same parallel-pair rule:

```
Z_viapair(ω)  = R_loop(ω) + jω L_loop                                  [Ω]  one PWR/GND via pair
n_pair_dec    = n_pad = vias_per_pad        (integer ≥ 1; else E_VIA_COUNT)
Z_via,dec(ω)  = Z_viapair(ω) / n_pair_dec                              (one decap via set)
w_dec         = w_p(n_p = n_pair_dec)                                  (§2.4.5 cluster port)
n_pad,PAD     = pad_via_count               (PWR/GND via pairs per PAD, integer ≥ 1)
Z_via,pad(ω)  = Z_viapair(ω) / n_pad,PAD                               (one PAD via set = per pad)
w_pad         = w_p(n_p = n_pad,PAD)                                   (every PAD port)
```

Above the planes, the n pairs of one via set are treated as ideal parallel paths, i.e. the loop
inductance and resistance of the set are those of one pair divided by n (mutual inductance between
parallel via pairs neglected — optimistic, see §9). Inside the cavity, the n cavity-crossing vias of
the set (one per pair) are represented by one wider cluster port (w_p from §2.4.5, checkerboard of
pitch √2·s_v), so additional pairs also reduce the cavity spreading inductance. The rule is identical
for decap and PAD via sets; they share the same h_near (§2.6.1). Values for D_drill = 0.2 mm,
s_v = 1 mm: n_pad = 1 → w_dec = 0.22369 mm (the §8 golden values), 2 → 0.84120 mm, 4 → 1.77893 mm.

Schema 1 stored `vias_per_decap` = total PWR + GND vias of a set (even, default 2, n_pair_dec =
vias_per_decap/2); migration 1 → 2 sets `vias_per_pad = max(1, ceil(vias_per_decap/2))` (odd
values had been rounded up to the next even count by the v0.1 GUI), so all schema-1 results are
unchanged (§4.7, §5.8.4).

#### 2.6.5 Dummy Cap option and port loads

A **dummy cap** is a capacitor without vias of its own that shares the via set of an adjacent
capacitor (both capacitors are connected to the same pads/traces feeding one via set). For a decap row
k with N_k capacitors:

* δ_k = false: P_k = N_k ports, every port loaded by **one** capacitor.
* δ_k = true: half of the capacitors are assumed to be dummies. P_k = ceil(N_k/2) ports.
  In port order (§2.5.3) within row k, port j (j = 0 … P_k−1) carries

  ```
  c_{k,j} = 2    for j < floor(N_k / 2)
  c_{k,j} = 1    for j = P_k − 1 when N_k is odd   (the last port of the row gets a single cap)
  ```

  so that Σ_j c_{k,j} = N_k always.
  * N_k = 1 → one port with one capacitor (the flag has no effect; info `I_DUMMY_SINGLE`).
  * N_k = 2 → one port, 2 caps. N_k = 4 → 2 ports [2, 2]. N_k = 5 → 3 ports [2, 2, 1].

Load of decap port p belonging to row k with c = c_{k,j} capacitors:

```
Z_cap,k(ω) = Z_decap,k(ω) + jω L_mount                       one capacitor incl. its own mounting
Z_L,p(ω)   = Z_cap,k(ω) / c  +  Z_via,dec(ω)                 [Ω]
```

(Two identical capacitors in parallel give Z_cap/2; the shared via set is in series and is not
divided.) Mutual inductance between the two parallel capacitors' current loops is neglected.

### 2.7 Decap models

#### 2.7.1 SPICE subcircuit (.mod) — AC modified nodal analysis

The two-terminal subcircuit is flattened (§4.5) into a list of R, L, C, K elements. Its impedance
between pin1 and pin2 is found by MNA **[Ho75], [Vlach94]**. Pin2 is the reference node (row/column
removed). Unknowns x = [V_1 … V_n, i_1 … i_b]ᵀ, where node voltages exclude the reference and
branch currents exist for every inductor and every zero-valued resistor.

System: `(A0 + jω A1) x = e`, with e = unit current injected into pin1 (e[pin1] = +1). Then
`Z_decap(ω) = V_pin1` [Ω] (1 A excitation).

Stamps (node index −1 = reference, skip those rows/cols):

| Element | Stamp |
|---|---|
| R between p,q, R ≠ 0 | G = 1/R: A0[p,p] += G, A0[q,q] += G, A0[p,q] −= G, A0[q,p] −= G |
| R = 0 | treated as branch with Z = 0: like L with L = 0 |
| C between p,q, C > 0 | A1[p,p] += C, A1[q,q] += C, A1[p,q] −= C, A1[q,p] −= C |
| C = 0 | ignored (open) |
| L between p,q (branch index b, row r = n + b) | A0[p,r] += 1, A0[q,r] −= 1 (KCL: current leaves p into element); A0[r,p] += 1, A0[r,q] −= 1 (branch eq V_p − V_q …); A1[r,r] −= L |
| K coupling L_a (row r_a), L_b (row r_b), coefficient k | M = k·sqrt(L_a L_b); A1[r_a,r_b] −= M; A1[r_b,r_a] −= M |
| gmin | A0[i,i] += 1e-12 S for every non-reference node (prevents floating-node singularity) |

Branch equation row r reads V_p − V_q − jωL_b i_b − Σ_c jωM_bc i_c = 0. Dot convention: the first
node of each inductor is its dotted terminal (SPICE convention **[Nagel75]**).
Validation: |k| ≤ 1 (error otherwise; k = 1 permitted with warning `W_K_UNITY`), K must reference
existing inductors in the same flattened scope, L ≥ 0, C ≥ 0 (negative → error), R < 0 → warning.

**Analytic check (series RLC):** Z = R + jωL + 1/(jωC); SRF f0 = 1/(2π√(LC)).
Example 0402 0.1 µF model (R = 30 mΩ, L = 0.45 nH, C = 100 nF): f0 = **23.73 MHz**, |Z(f0)| = 30 mΩ.
Example 0603 10 µF model (L = 0.5 nH, C = 10 µF): f0 = **2.251 MHz**, |Z(f0)| ≈ 5 mΩ.

#### 2.7.2 Touchstone .s2p (optional)

A 2-port S-parameter measurement of a capacitor mounted in a fixture, reference impedance Z0 (from
the option line, default 50 Ω). With ABCD matrices [[1, Z],[0, 1]] (series element) and
[[1, 0],[1/Z, 1]] (shunt element) and S21 = 2/(A + B/Z0 + C·Z0 + D) **[Pozar12]**:

| Configuration | S21 | Z from S21 |
|---|---|---|
| **Series-through** (DUT between port 1 and port 2 centre conductors) | S21 = 2Z0/(2Z0 + Z) | **Z = 2·Z0·(1 − S21)/S21** |
| **Shunt-through** (DUT from the through line to ground) **[Novak00], [Novak07]** | S21 = 2Z/(2Z + Z0) | **Z = Z0·S21 / (2·(1 − S21))** |

Note: the expression "Z = 2·Z0·S21/(1 − S21)" that appears in some notes is **incorrect** for
either configuration; the two formulas above are the ones derived from the ABCD relations and MUST
be used. To reduce measurement noise, use S21_avg = (S21 + S12)/2. Selection per decap row
(`S2P Mode` column) or project default; project default = **series-through**. If |S21| < 1e-12
(series) or |1 − S21| < 1e-12 (shunt, complex distance), clamp to that distance and emit
`W_S2P_SINGULAR`.

Note (repeated in Help): for |Z| ≪ Z0 the series-through form depends on 1 − S21 ≈ Z/(2Z0), i.e.
5e-5 at 5 mΩ, which is below typical VNA |S21| uncertainty (~1e-3). Shunt-through is the preferred
fixture for decaps near and above their SRF **[Novak00]**. The default remains series-through (user
requirement); the Help page recommends choosing `shunt` for shunt-through vendor files.

Interpolation onto the sweep grid: see §3.8.

### 2.8 Combining into Z at the PAD

Ports 0 … N_pad−1 = the PADs (set 𝒫); ports N_pad … N_pad+K−1 = all decap via sets of the PWR in
the port order of §2.5.3 (set 𝒦, K = Σ_k P_k). Partition the cavity matrix:

```
Z_cav = [ Z_PP   Z_PK ]        Z_PP: N_pad × N_pad,  Z_PK = Z_KPᵀ: N_pad × K
        [ Z_KP   Z_KK ]        (for N_pad = 1: Z_PP = z_00, Z_PK = z_0Kᵀ)
```

Terminating decap port k with load Z_L,k means V_k = −Z_L,k I_k. Eliminating the decap ports
(Schur complement / port reduction **[Swaminathan07], [Novak07]**) leaves the N_pad-port matrix of the
pads:

```
Z_L,p(ω)      = Z_cap,k(p)(ω) / c_p + Z_via,dec(ω)                      (§2.6.5)
Z_pp,red(ω)   = Z_PP − Z_PK (Z_KK + diag(Z_L))⁻¹ Z_KP                    N_pad × N_pad
```

**Pads in parallel.** The pads belong to one net and are joined on the die/probe side at one
common node (ideal: zero impedance between the pads, no die or package model, §9 item 11). Pad p
carries current I_p through its own via set, so the common-node voltage is
V = V_p + Z_via,pad I_p for every p, with V_𝒫 = Z_pp,red I_𝒫 and the total current I = 1ᵀ I_𝒫.
Hence (Z_pp,red + Z_via,pad·𝟙) I_𝒫 = V·1 and

```
A(ω)          = Z_pp,red(ω) + diag(Z_via,pad(ω), …, Z_via,pad(ω))       N_pad × N_pad
Z_PAD(ω)      = 1 / ( 1ᵀ A(ω)⁻¹ 1 )                                      [Ω]
Z_plane(ω)    = 1 / ( 1ᵀ (Z_PP + diag(Z_via,pad))⁻¹ 1 )                  ("plane only", optional curve)

N_pad = 1:  Z_red = z_00 − z_0Kᵀ (Z_KK + diag(Z_L))⁻¹ z_K0,
            Z_PAD = Z_red + Z_via,pad,   Z_plane = z_00 + Z_via,pad       (v1.3 equations, exact)
```

All quantities are complex; the plotted value is |Z_PAD| = sqrt(Re² + Im²). The products are
computed by solving linear systems (LU, never an explicit inverse), vectorised over frequency:
(Z_KK + diag(Z_L)) U = Z_KP with the N_pad columns of Z_KP as right-hand sides of one factorisation,
Z_pp,red = Z_PP − Z_PK U; then A y = 1 and Z_PAD = 1/Σ_p y_p. Both solves use the non-finite / rcond
safeguards of §3.6 (`E_SINGULAR`). For N_pad = 1 the implementation keeps the v1.3 code path
(u = solution for z_K0, Z_red = z_00 − z_0Kᵀu, Z_PAD = Z_red + Z_via,pad), so N_pad = 1 results are
bit-identical to v1.3. With K = 0 (no decaps), Z_pp,red = Z_PP and Z_PAD = Z_plane.

Properties (tested, §8.11): N_pad coincident pads (identical rows and columns of Z_cav) give
exactly z + Z_via,pad/N_pad, i.e. one PAD with N_pad times the via pairs at the same port width;
four single-via pads at the §2.4.5 cluster positions reproduce the explicit-port loop inductance
0.16653 nH, which the one-port cluster model approximates within 0.6 %. Pads placed by §2.5.1
(spread across the width) are **not** geometrically equivalent to one cluster pad of
N_pad·pad_via_count vias: for the VDD_IO example, 4 pads × 1 PAD via pair give 425.9 mΩ @100 MHz
versus 445.7 mΩ for one pad with 4 PAD via pairs (§8.11 #4), because the spread pads also shorten
the spreading path to the decaps across the width.

**Low-frequency asymptote.** No VRM or DC source is modelled, so Z_PAD → 1/(jω C_total) at low
frequency, with C_total = C_plane + Σ decap capacitances (if the decap models have a DC-blocking
capacitor). The curve is capacitive (slope −20 dB/decade) at the low end. This is a documented
limitation (§9); a VRM/bulk R-L element is not part of v1.

Design-time sanity (bundled example VDD_CORE, §4.7): 10 × 100 nF + 4 × 10 µF = 41 µF →
|1/(ωC_total)| at 100 kHz = 38.82 mΩ; prototype Z_PAD = 38.69 mΩ (N_pad does not change C_total). VDD_IO (dummy row of 4 × 100 nF on
2 via sets + 1 × 10 µF): 10.4 µF → 153.0 mΩ; prototype 152.5 mΩ.

---

## 3. Numerics

### 3.1 Frequency grid

```
f = numpy.geomspace(f_start, f_stop, n_points)        [Hz]
```

Defaults: f_start = 1e5, f_stop = 1e9, n_points = 400. Constraints (validation):

* 1e3 ≤ f_start < f_stop (floor 1 kHz: below it the plane term 1/(ωC) ~ MΩ and MNA gmin/scale
  issues start; error `E_SWEEP_RANGE`).
* f_stop ≤ 2e10 (hard); warning `W_SWEEP_HIGH` if f_stop > 3e9 (model validity: d ≪ λ, no
  radiation, no via capacitance).
* 10 ≤ n_points ≤ 5000.

Marker frequencies F_mk = {1e6, 1e7, 1e8} Hz. The engine evaluates Z at the union of the grid and
the marker frequencies inside [f_start, f_stop] (sorted, deduplicated), returns the grid values for
plotting and the **exact** marker values for readouts (no interpolation error). Markers outside the
range show "n/a".

### 3.2 Mode truncation

```
k_max   = sqrt( max over f_eval of |k²(ω)| )       [rad/m]  (§2.4.1, lossy; ≥ k0_max = 2π f_stop √εr_eff / c0)
K_split = 4 · k_max
w_min   = min_p w_p                                  (smallest port width, §2.4.5)
M = min(M_cap, max(16, ceil(K_split·a/π), ceil(3·a/w_min)))
N = min(M_cap, max(16, ceil(K_split·b/π), ceil(3·b/w_min)))
M_cap = 1500
```

* ceil(K_split·a/π) covers all modes up to 4× the highest excitation wavenumber (≥ 3× required).
  k_max includes conductor and dielectric loss (|k²| exceeds k0² by ≈ 1 % for the example stack),
  so the bound of §3.3 holds.
* ceil(3a/w) extends the sum beyond the first sinc null (m = 2a/w) by 50 %, making the port
  spreading-inductance converge (design-time check: 3·a/w, 6·a/w, 12·a/w give 0.1482, 0.1483,
  0.1483 nH in the loop-L case of §2.4.5).
* If M or N hits M_cap, emit `W_MODES_CAPPED` (spreading inductance slightly under-resolved; accuracy
  degrades for very large planes with very small drills).

### 3.3 Low/high mode split (quasi-static tail extraction)

Split the index set into a **dynamic** set 𝓛 = {(m,n) : k_mn ≤ K_split} (always contains (0,0))
and a **static** set 𝓗 (all others). For (m,n) ∈ 𝓗, |k²/k_mn²| ≤ 1/16 over the sweep, so

```
1/(k_mn² − k²) = 1/k_mn² + k²/k_mn⁴ + O(k⁴/k_mn⁶)      (relative error of dropped part ≤ (1/16)²/(1 − 1/16) ≈ 1/240)
```

Review evidence (20 × 20 mm, 3 ports, Cu, tanδ 0.02): split vs brute-force dynamic sum, max relative
error of Z entries 5.5e-17 (1 kHz), 7.7e-16 (1 MHz), 5.9e-11 (100 MHz), 1.25e-4 (1 GHz); dropping S1
raises the 1 GHz loop error to 2.9e-3, so S1 MUST be kept.

Define the separable port factors (real):

```
X_{i,m} = sqrt(C_m) · cos(mπ x_i / a) · sinc_u(mπw_i/(2a))      shape (P, M+1)
Y_{i,n} = sqrt(C_n) · cos(nπ y_i / b) · sinc_u(nπw_i/(2b))      shape (P, N+1)
u_{i,mn} = X_{i,m} Y_{i,n}
```

Precompute once per PWR (frequency independent, real float64):

```
S0_ij = Σ_{(m,n)∈𝓗} u_{i,mn} u_{j,mn} / k_mn²          [m²]
S1_ij = Σ_{(m,n)∈𝓗} u_{i,mn} u_{j,mn} / k_mn⁴          [m⁴]
U_L   = [u_{i,mn}]_{(m,n)∈𝓛}                           shape (P, L)
κ_L   = [k_mn²]_{(m,n)∈𝓛}                              shape (L,)
```

Per frequency:

```
Z(ω) = (Z_p(ω)/(ab)) · [ U_L · diag(1/(κ_L − k²(ω))) · U_Lᵀ + S0 + k²(ω)·S1 ]
```

Efficient evaluation of S0, S1 (MUST be done row-by-row in m to bound memory):

```python
for m in range(M+1):
    kmn2 = kx2[m] + ky2                        # shape (N+1,)
    w0 = where(is_static[m], 1/kmn2, 0)        # (0,0) is never static
    w1 = w0**2
    XX = outer(X[:, m], X[:, m])               # (P,P)
    S0 += XX * ((Y * w0) @ Y.T)
    S1 += XX * ((Y * w1) @ Y.T)
```

(This is pseudocode describing the algorithm, not copied code.)

### 3.4 Loss floor

To avoid a division by zero at an exact lossless resonance (tanδ = 0 and σ = ∞), the engine uses
tanδ_used = max(tanδ_eff, 1e-6) inside k² and Y_p. The C_plane reported in the GUI uses the real εr
only.

### 3.5 Complexity estimates

Let P = 1 + K ports, L = |𝓛|, F = number of frequencies.

| Stage | Cost | Typical (P=51, a=b=100 mm, D=0.2 mm → M=N=1342, f_stop=1 GHz) |
|---|---|---|
| X, Y factors | P(M+N) | trivial |
| S0, S1 | 2·P²·M·N real flops, M+1 BLAS calls | ≈ 1.2e10 flops, 2–5 s |
| Dynamic sum | F·P²·L complex | L ≈ 30 → 3e7, trivial; at f_stop = 10 GHz L ≈ 2000 → 2e9, ~5 s |
| Schur reduce | F·K³/3 | 400·4.2e4 ≈ 2e7, trivial |
| Decap MNA | F·n³/3 per distinct model | trivial (n < 50) |

The costs above describe the v0.1.0 loop of §3.3; the evaluation actually used (grouped static sums,
threaded frequency chunks) and measured timings are in §3.9.

Memory: U_L ≤ P·L·8 bytes; batched Schur (F, K, K) complex ≤ 400·2500·16 = 16 MB. Decap impedances
are computed once per **distinct** (file, subckt, s2p mode) and reused.

### 3.6 Schur reduction implementation

For all frequencies at once (vectorised): build `A[f] = Z_KK[f] + diag(Z_L[f])` shape (F,K,K),
`rhs[f] = z_K0[f]` shape (F,K,1); `u = numpy.linalg.solve(A, rhs)`; `Z_red = z_00 −
sum(z_0K * u[...,0], axis=-1)`. If F·K²·16 B > 256 MB, process in frequency chunks.
`numpy.linalg.LinAlgError` → error `E_SINGULAR` with the PWR name and frequency.
`numpy.linalg.solve` raises only for exactly singular pivots, so after the solve the engine MUST
also check: if any Z_red (or Z_PAD) is non-finite, or the reciprocal condition number
`rcond = 1/numpy.linalg.cond(A)` (batched, 2-norm) is < 1e-14 at any frequency → `E_SINGULAR`
(message names the first offending frequency and suggests checking `W_PORT_OVERLAP` / zero-length
vias with ideal-short models). Legitimate cases have rcond ≫ 1e-14 (bundled example over the default grid: min rcond 2.4e-6 for VDD_CORE, 2.5e-5 for VDD_IO).

**Several PADs (N_pad ≥ 2, §2.8).** The right-hand side becomes the N_pad columns of Z_KP (shape
(F,K,N_pad), solved in the same LU call as the probe vectors), and Z_pp,red = Z_PP − Z_PK U is
formed with a batched `matmul`. The pad combination A y = 1 (A = Z_pp,red + Z_via,pad·I, shape
(F,N_pad,N_pad)) is a second batched solve (`pdn._combine_pads`) with its own probe estimate,
selective exact SVD and `E_SINGULAR` check (message "ill-conditioned PAD-combination matrix");
`min_rcond` is the minimum over both systems. Both run in frequency chunks on the worker threads of
§3.9. Z_via,pad on the diagonal keeps A well conditioned even for coincident pads (via resistance
> 0); A is singular only for coincident pads with zero via impedance. The plane-only curve uses
the same combination on Z_PP without the rcond check.

**rcond screening (post-v0.1.0, `pdn._reduce`).** The batched SVD is ≈ 5× the cost of the solve
(K = 120: 0.59 s vs 0.10 s for 403 frequencies), so it is computed only where it can matter:

1. *Probe estimate in the same factorisation.* k = min(8, K) fixed complex Gaussian unit vectors
   v_j (seed depends only on K) are appended as extra right-hand-side columns of the one
   `numpy.linalg.solve` call (one LU per frequency; the first column's solution is bit-identical to
   solving the Schur RHS alone). L = max_j ‖A⁻¹v_j‖₂ ≤ ‖A⁻¹‖₂ and
   rc_est = 1/(‖A‖_F·L). Deterministically rc_est ≥ rc/√K (since ‖A‖_F ≤ √K‖A‖₂ and L ≤ ‖A⁻¹‖₂);
   rc_est over-estimates rc by more than γ only if |⟨u₁, v_j⟩| < 1/γ for **all** k probes (u₁ = the
   right singular vector of σ_min), probability ≈ (K/γ²)^k for generic matrices — 1e-40 for K = 1000,
   γ = 1e4, k = 8 (random-probe condition estimation, cf. Dixon 1983; the LAPACK `xGECON`/Hager–Higham
   1-norm estimator is likewise an estimate).
2. *Exact SVD where it can matter:* every frequency with rc_est < γ·1e-14 (γ = 1e4) and the 8
   frequencies with the smallest rc_est. The `E_SINGULAR` decision uses exact SVD values only, so a
   flagged frequency, its reported rcond and the first offending index are the same as with the full
   SVD; `min_rcond` is the exact minimum over the refined set (bit-identical on the example and the
   benchmark cases; the estimate is within [0.47, 3.8]× of rc there and the minimum lies among the 8
   smallest estimates).
3. Non-finite solutions or estimates give rc_est = 0 (→ exact check); non-finite Z_red is still
   checked at every frequency. A non-finite matrix now reports its own frequency instead of the first
   frequency of its chunk.

Alternatives measured and rejected: an explicit batched inverse for an exact ‖A⁻¹‖ (0.79 s, slower than
the SVD — numpy exposes no LU factors, so a factor-based `xGECON`-style estimator would need repeated
factorisations, ≥ 4 solves ≈ 0.4 s, and scipy is excluded from the build); SVD on a frequency
subsample with residual checks ‖Ax − b‖/‖b‖ — partial-pivoting LU is backward stable, so the residual
stays ≈ ε even for rcond ≪ 1e-14 and cannot replace the rcond check at unsampled frequencies.
`_reduce(..., exact_rcond=True)` keeps the full-SVD behaviour (tests).

### 3.7 Numerical pitfalls and rules

1. **Low-frequency cancellation.** All entries of Z contain the same huge (0,0) term 1/(jωC_plane).
   In the Schur complement this cancels with a round-off of about ε_mach·|1/(ωC)|: at 1 kHz with
   C = 100 pF this is ≈ 3.5e-10 Ω, negligible against decap impedances (≥ mΩ). The 1 kHz floor keeps
   this safe. (Optional future improvement: treat the (0,0) term as a rank-one update with
   Sherman–Morrison.)
2. **L = 0 or R = 0** → handled as zero-impedance branches with a current unknown (no 1/0).
3. **C = 0** → element omitted. **Floating nodes** → gmin 1e-12 S; additionally a graph check
   warns `W_MNA_FLOATING` if a node has no path to pin2 through R/L elements or through C.
4. **pin1 = pin2** in the top-level subckt → error.
5. **MNA scaling:** entries range from 1e-12 (gmin) to 1e3; double precision is sufficient for
   n < 200. For decap models with more than 200 unknowns, loop over frequencies instead of batching.
6. **s2p extrapolation:** see §3.8.
7. **Log axes:** |Z| is clamped to ≥ 1e-15 Ω before plotting to avoid log(0).
8. **sinc** at m = 0: use `np.sinc` (defined at 0) — never compute sin(u)/u manually.
9. **coth** for small arguments: series branch (§2.4.2).
10. **Via lengths** use z_top of the nearer plane measured from the Top surface (z = 0, §2.6.1); all components are on Top.

### 3.8 Touchstone interpolation / extrapolation

Convert S → Z at the file's frequencies (§2.7.2). For grid frequencies inside
[f_first, f_last]: linear interpolation in log10(f) of log|Z| and of the **unwrapped** phase of Z.
Outside:

* f < f_first: fit Z ≈ R_x + 1/(jωC_x) to the first point: R_x = Re Z_1, C_x = −1/(ω_1 Im Z_1) if
  Im Z_1 < 0; else hold Z_1 constant. Emit `W_S2P_EXTRAP_LOW`.
* f > f_last: fit Z ≈ R_x + jωL_x to the last point: L_x = Im Z_n / ω_n if Im Z_n > 0; else hold
  constant. Emit `W_S2P_EXTRAP_HIGH`.

### 3.9 Performance architecture

The numerics of §3.1–§3.8 are unchanged; this section fixes *how* they are evaluated. All paths are
Qt-free and use numpy only (`threadpoolctl` is used when importable, never required).

**Static sums, grouped by distinct port factors** (`cavity._static_sums`). Write
S_ij = Σ_m X_im X_jm C_ij,m with C_ij,m = Σ_n Y_in Y_jn W_mn, where W0 = 1/k_mn² on 𝓗 (else 0) and
W1 = W0² are built once as (M+1)×(N+1) arrays (≤ 18 MB each at the 1500-mode cap). All ports of a
decap (sub-)row have bit-identical Y rows (same y, same width), so the n-sum is needed only for pairs
of **distinct** rows: for each distinct row r, `B = Ŷ_r ∘ Ŷ_{s≥r}` and `C = B·Wᵀ` (one GEMM), then
the (P_r × P_{s≥r}) block `X_I·(X_J ∘ C)ᵀ` (one GEMM), mirrored for symmetry. Cost
R²/2·(M+1)(N+1) + P²(M+1) instead of 2·P²·(M+1)(N+1) (R = distinct rows; the axis with fewer
distinct rows is chosen). Exactly the same terms are summed; only the grouping (rounding ≈ 1e-16
relative) differs. The worst case (every port on its own row) costs no more than the §3.3 loop and
needs only O(R·(M+N) + P·M) extra memory.

The grouping key is the actual port-factor row — i.e. the exact (y, w) of a port (resp. (x, w) for the
x axis) — never the decap row index, so ports with equal y still group. With the `normal` distance
distribution (§2.5.5) every decap port has its own y and the y grouping degenerates to R = P; the
x axis (ports of rows with equal P_k share their x cells) is then chosen automatically. Measured
(`tools/bench.py --distance normal`, best of 3–5, cold caches, 2-core Xeon): large MLO 0.29 s fixed →
0.75 s normal with 2 workers (static sums 0.07 → 0.53 s), 0.42 → 0.82 s with 1 worker; example
0.035 → 0.040 s; many nets 0.10 → 0.13 s — below the 3× threshold, so **no quantisation of y** is
applied (a 10 µm grid would not help anyway: the 121 sampled y values of the large case still fall
into 113 distinct bins, and it would change results).

**Dynamic sum and Z assembly** (`CavityModel.z_matrix_into`). The (L, P²) real table
T_l = u_l u_lᵀ is built once (≤ 64 MB, otherwise a per-frequency row product is used); per frequency
chunk `Re dyn = Re g · T`, `Im dyn = Im g · T` are two real GEMMs with g = 1/(κ_L − k²). Then
D = dyn + S0 + k²S1 and Z = pref·D are formed with the explicit real/imaginary formulas of complex
multiplication, in place, per chunk (no F×P×P temporaries).

**Frequency chunks and worker threads** (`core/parallel.py`). Z(ω) and the Schur reduction (§3.6:
batched `solve` with rcond probes, then the selective exact SVD) are split into contiguous frequency chunks of ≈ 8 MB
working set (at least 2·workers chunks when the work exceeds 2 MB) and run on a private
`ThreadPoolExecutor`; numpy releases the GIL inside LAPACK/BLAS and ufunc loops. Very small chunks
are avoided (they lose the gain to GIL hand-offs between numpy calls). Each chunk performs exactly
the per-frequency arithmetic of the serial code, so results do not depend on the worker count
(tested ≤ 1e-12, in practice bit-identical). The first offending frequency of `E_SINGULAR` is the
lowest index over all chunks, as in the serial loop.

**PWR nets in parallel** (`engine.compute_project`). With `workers` threads and n nets,
min(workers, n) nets run concurrently, each with ⌊workers / that⌋ chunk threads. Per-net error
isolation, result order (PWR table order) and issue order are identical to the serial run. Progress
is the mean of the per-net fractions (non-decreasing, emitted under a lock); cancel is polled by
every chunk and net task, and any cancellation stops the other tasks before `CancelledError` is
raised. `advanced.workers` (§4.7, GUI Vias → Advanced → Worker threads): 0 = auto =
`os.cpu_count()`.

**Threads vs processes** (measured with `tools/bench.py --compare-executors`, 2-core Xeon, numpy 2.4
/ OpenBLAS 0.3.31): six nets — threads 0.106 s, spawn process pool 0.283 s cold (interpreter + numpy
import per process) and 0.078 s with an already warm pool; one large net — threads 0.68 s, processes
1.07 s. Processes only win when a pool is kept alive across runs, would need picklable
progress/cancel channels and a frozen-exe bootstrap, and duplicate the caches. **Threads are used.**
(`app.main` still calls `multiprocessing.freeze_support()` so a future process pool is safe in the
PyInstaller build.)

**BLAS thread policy.** The hot operations are many small dense kernels, for which a multithreaded
BLAS is slower and oversubscribes the cores (batched 120×120 complex SVD: 0.55 s with 1 BLAS thread,
1.46 s with 2). Therefore `compute_project` / `compute_pwr` run inside `parallel.blas_limited(1)`
(re-entrant, process-wide, via `threadpoolctl` when importable), and `app.main` sets
`OPENBLAS_NUM_THREADS`/`OMP_NUM_THREADS`/`MKL_NUM_THREADS` to 1 before numpy is imported unless the
user has set them (so the frozen build behaves the same without `threadpoolctl`).

**Caches.**

| Cache | Key | Invalidated by | Not invalidated by | Bound |
|---|---|---|---|---|
| Cavity Z-matrix (`cavity.CavityCache`, one per `EngineBridge`; module default for headless use) | SHA-256 of plane W×H, the `PlanePair` (layers, thicknesses, σ, Dk/Df, d, εr_eff, tanδ_eff), port xy and widths (PAD row and decap rows), N_pad (only when ≠ 1, so N_pad = 1 keys are unchanged), evaluation frequencies (grid ∪ markers), `ModeSettings`; in `normal` distance mode (§2.5.5) also `(mode, σ, seed)` and the sampled per-port distances (fixed mode keeps the 0.2.0 key) | distance mode, σ and seed (normal mode), plane width, **Number of PADs** (pad positions and count), decap row count/distance/dummy/enable (placement), drill, via pitch, vias per decap pad, PAD vias (port widths), stack-up of the pair, sweep | σ and seed in fixed mode, decap model file/subckt/S2P mode, via model, plating, via σ, anti-pad, mounting inductance, show plane-only (these enter only the loads Z_L and Z_via,pad, which are recomputed on every run; PAD vias also changes w_pad and therefore the key) | 32 entries and 512 MB, LRU; stored read-only |
| Decap model (`DecapModelCache`, existing) | abs path, size, SHA-256 of the file content, subckt, S2P mode (content, not mtime: a same-size edit that keeps the modification time — coarse FAT/SMB clocks, tools restoring mtimes — still invalidates; review v0.2) | file edit | touch without content change | unbounded (small) |
| Decap impedance (memo on each cached model object) | exact evaluation frequency vector | new model object (file edit, subckt, mode), sweep | everything else | 8 sweeps per model; warnings replayed on hits |

`evaluation_frequencies` (§3.1) is computed once per net; the grid ∪ marker vector is evaluated in a
single pass. The static sums, port factors (cos/sinc tables) and T are frequency independent and
computed once per net.

**Measured** (`tools/bench.py`, best of 3, cold caches, 2 cores; "before" = v0.1.0 code):

| Scenario | before | after, workers = 1 | after, auto (2) | decap-only re-run (cache) |
|---|---|---|---|---|
| §8 example (2 nets, P = 15/4) | 0.049–0.054 s | 0.023 s | 0.023–0.029 s | 0.006–0.009 s |
| large MLO (P = 121, M×N = 1073×845, 400 pts) | 2.87–3.60 s | 0.26–0.29 s | 0.18–0.22 s | 0.09 s |
| many nets (6 nets, P = 13–23) | 0.22–0.27 s | 0.08 s | 0.05–0.06 s | 0.02 s |

With the rcond screening of §3.6 (probe estimate + selective SVD) the large-MLO reduction dropped
from 0.34 s to 0.08–0.10 s (full-SVD variant of this section: 0.45 s total with 2 workers, 0.82 s
with 1); the remaining time is split evenly between static sums, Z(f) and the reduction.

---

## 4. Data formats

### 4.1 Excel general rules (all three tables)

* Library: openpyxl, `load_workbook(path, read_only=True, data_only=True)`.
* Sheet choice: the first sheet whose normalised name contains the table keyword (`stack`, `pwr` /
  `power`, `decap` / `cap`); otherwise the active sheet.
* **Header row detection:** scan rows 1…20; for each row count matched **required** columns (rules
  below); the header row is the first row with the maximum count, which must equal the number of
  required columns, else error `E_XL_HEADER_NOT_FOUND` listing the missing columns and the best
  candidate row.
* Data rows: all rows below the header; rows whose cells are all empty are skipped; parsing stops at
  the first row whose first required column is empty **and** all other required cells are empty.
* **Normalisation of a header string** `norm(h)`: str → Unicode NFKC → casefold → replace
  `μ`/`µ` with `u`, `δ` with `d`, `ε` with `e` → remove characters other than `[a-z0-9()/#%]` →
  unit text inside parentheses is extracted separately as `unit(h)` (e.g. `thickness(mm)` → base
  `thickness`, unit `mm`).
* **Matching:** each column rule is a list of predicates on `base = norm(h)` without the unit part.
  Rules are applied in the listed order; a header cell is consumed by the first rule it matches;
  each rule takes the first matching cell (left to right). Two cells matching the same rule → the
  second is ignored with warning `W_XL_DUP_COLUMN`.
* **Numeric cells:** int/float accepted; strings are parsed with the SPICE-free numeric parser
  `float(s.replace(',', '.'))` after stripping whitespace (e.g. `"5.8E7"`); failure → error
  `E_XL_NUMBER` with sheet, cell reference (e.g. `C7`), and text. Formula cells with no cached value
  (None) → error `E_XL_FORMULA_NO_VALUE` ("open and save the file in Excel").
* Length units from the header unit: `mm` (default when absent), `um` → ×1e-3 mm, `mil` → ×0.0254 mm,
  `m` → ×1000 mm, `in`/`inch` → ×25.4 mm. Unknown unit → error `E_XL_UNIT`.

### 4.2 Stack-up table

| Field | Required | Rule (on normalised base) | Type / unit |
|---|---|---|---|
| layer_name | no | contains `layer` and contains `name` | str |
| layer_number | yes | (contains `layer` and any of `number`, `num`, `no`, `#`, `idx`, `index`) or base ∈ {`layer`, `layerno`, `no`, `#`} | int |
| thickness | yes | contains `thick` or base ∈ {`t`, `th`} | float, length unit (default mm) |
| conductivity | yes (column must exist; cells may be empty) | contains `conduct` or `sigma` or unit is `s/m` | float S/m |
| dk | yes | base ∈ {`dk`, `er`, `epsr`, `eps`} or contains `permittivity` or `dielectricconstant` | float |
| df | yes | base ∈ {`df`, `tand`, `tandelta`, `losstangent`} or contains `dissipation` or `losstan` | float |

(`layer_name` is listed first so that "Layer Name" is not consumed by the layer_number rule.)

**Row classification:**

* conductivity cell numeric and > 0 → **metal**.
* conductivity empty, 0, or text `-`/`n/a` → **dielectric**.

**Validation:**

| Code | Severity | Condition |
|---|---|---|
| E_STACK_EMPTY | error | no data rows |
| E_STACK_LAYER_NUM | error | layer number missing / non-integer / < 1 |
| E_STACK_LAYER_DUP | error | duplicate layer numbers |
| W_STACK_LAYER_GAP | warning | numbers not contiguous 1..N after sorting (rows are sorted; numbering kept) |
| E_STACK_THICKNESS | error | thickness missing or ≤ 0 |
| E_STACK_DK | error | dielectric row with Dk missing or < 1 |
| W_STACK_DF_MISSING | warning | dielectric row with Df missing → 0 used |
| E_STACK_DF | error | Df < 0 or Df > 1 |
| W_STACK_SIGMA_RANGE | warning | metal σ outside [1e5, 1e8] S/m |
| I_STACK_FILL_DKDF | info | Dk/Df on metal rows read as fill-in material properties; not used by the model (once per stack-up) |
| W_STACK_ADJ_METAL | info | two metal rows adjacent without dielectric (allowed) |

**Example (bundled `examples/stackup_6L.xlsx`, sheet `Stackup`, header in row 1):**

| Layer Number | Layer Name | Thickness(mm) | Conductivity(S/m) | Dk | Df |
|---|---|---|---|---|---|
| 1 | TOP | 0.035 | 5.8E7 | 4.2 | 0.02 |
| 2 | PP1 | 0.1 | | 4.2 | 0.02 |
| 3 | GND1 | 0.035 | 5.8E7 | 4.3 | 0.018 |
| 4 | CORE1 | 0.1 | | 4.3 | 0.018 |
| 5 | PWR1 | 0.035 | 5.8E7 | 4.3 | 0.018 |
| 6 | PP2 | 0.8 | | 4.4 | 0.02 |
| 7 | PWR2 | 0.035 | 5.8E7 | 4.3 | 0.018 |
| 8 | CORE2 | 0.1 | | 4.3 | 0.018 |
| 9 | GND2 | 0.035 | 5.8E7 | 4.2 | 0.02 |
| 10 | PP3 | 0.1 | | 4.2 | 0.02 |
| 11 | BOTTOM | 0.035 | 5.8E7 | 4.2 | 0.02 |

**Fuzzy header test cases** (all must map correctly): `Layer No.`, `LAYER #`, `layer_number`,
`Thickness (mm)`, `thickness[um]` (bracket units: treat `[]` like `()`), `Thk(mil)`,
`Conductivity (S/m)`, `Sigma`, `DK`, `Er`, `εr`, `Df`, `tan δ`, `Loss Tangent`, `Dissipation Factor`.

### 4.3 PWR list table

| Field | Required | Rule | Type |
|---|---|---|---|
| pwr_name | yes | contains `pwr` or `power` or `net`, and contains `name` or base ∈ {`pwr`, `net`, `rail`} | str (unique, trimmed, case-sensitive) |
| gnd_layer | yes | contains `gnd` or `ground` (and `layer`) | int |
| pwr_layer | yes | contains `layer` (after gnd rule consumed its cell) | int |
| width | yes | contains `width` or base ∈ {`w`, `x`} | float, length unit (mm default) |
| n_pads | no | contains `pads`, or contains `pad` and any of `count`, `number`, `num`, `qty`, `quantity`, `#` (e.g. `Number of PADs`, `PAD Count`, `# PADs`, `PADs`) | int ≥ 1; column absent or cell empty → 1 |

Rule order: n_pads, gnd_layer, pwr_layer, pwr_name, width.

A column whose header contains `height`, `length`, or `pad` (legacy files) and does **not** match
the n_pads rule is ignored with warning `W_XL_COLUMN_IGNORED` ("plane height and PAD position are
derived automatically"), e.g. `PAD X (mm)`, `PAD Position`.

Validation: `E_PWR_NPADS` (number of PADs not an integer ≥ 1, with cell reference), `E_PWR_NAME_DUP`, `E_PWR_LAYER_NOT_FOUND`, `E_PWR_LAYER_NOT_METAL` (either layer is
dielectric), `E_PWR_SAME_LAYER`, `E_PWR_DIM` (width ≤ 0), `E_PWR_WIDTH_TOO_SMALL` (§2.5.3),
`W_STACK_METAL_BETWEEN` (§2.2), `W_PWR_FAR_GND` if the PWR and GND layers are separated by more than
3 intermediate layers, `W_PWR_NO_DECAPS` (no enabled decap rows: square plane H = W, plane-only
result).

Example (`examples/pwr_list.xlsx`):

| PWR Name | Layer Number | GND Layer Number | PWR Plane Width | Number of PADs |
|---|---|---|---|---|
| VDD_CORE | 5 | 3 | 60 | 1 |
| VDD_IO | 7 | 9 | 30 | 1 |

(Both nets keep one PAD so that the §8.11 golden values apply to the bundled example.)

### 4.4 Decap assignment table

| Field | Required | Rule | Type |
|---|---|---|---|
| pwr_name | yes | contains `pwr` or `power` or `net` or `rail` | str; must exist in PWR list |
| distance | yes | contains `dist` | float, length unit (mm default), > 0 |
| count | yes | contains `number` or `count` or `qty` or `quantity` or `#` or base = `n` | int ≥ 1 |
| model_file | yes | contains `file` or `model` or `decap` (after the other rules) | str path |
| subckt | no | contains `subckt` or `subcircuit` | str |
| s2p_mode | no | contains `s2p` or `mode` or `config` | `series`/`shunt` (prefix match, case-insensitive) |
| dummy | no | contains `dummy` | bool; column absent → false for all rows |

Rule order: subckt, s2p_mode, dummy, distance, count, pwr_name, model_file.

**Boolean cells** (`dummy`): openpyxl `bool` → as is; numbers 1 / 0 → true / false; strings
(trimmed, case-insensitive) `yes`, `y`, `true`, `1`, `x`, `✓` → true; `no`, `n`, `false`, `0`, empty
→ false; anything else → error `E_XL_BOOL` with cell reference.

Path resolution for `model_file` (first existing wins): absolute path; relative to the Excel file's
folder; relative to the project file's folder; relative to the project setting
`model_search_dir`. Not found → `E_DECAP_FILE_NOT_FOUND`. Extension `.mod`, `.lib`, `.sp`, `.cir`,
`.sub`, `.inc` → SPICE parser; `.s2p` → Touchstone reader; others → `E_DECAP_FILE_TYPE`.

Example (`examples/decap_list.xlsx`):

| PWR Name | Decap File Name | Number of Decaps | Distance to PAD (mm) | Dummy Cap |
|---|---|---|---|---|
| VDD_CORE | cap_0402_100nF.mod | 10 | 8 | No |
| VDD_CORE | cap_0603_10uF.mod | 4 | 15 | No |
| VDD_IO | cap_0402_100nF.mod | 4 | 5 | Yes |
| VDD_IO | cap_0603_10uF.mod | 1 | 10 | No |

Derived geometry of this example: VDD_CORE W = 60 mm, D_ref = 15 mm, H = 21 mm, 14 decap ports;
VDD_IO W = 30 mm, D_ref = 10 mm, H = 14 mm, 3 decap ports (2 ports × 2 caps at y = 7 mm,
x = 9.0559 / 20.9441 mm; 1 port × 1 cap at (15, 12) mm), PAD (15, 2) mm.

### 4.5 SPICE .mod grammar and semantics

**Lexical preprocessing (in this order):**

1. Read as UTF-8 with `errors="replace"`; also accept Latin-1 (retry on decode error).
2. Lines whose first non-blank character is `*` → comment.
3. Inline comments: `;` anywhere outside `{}`/`''`, and `$` when preceded by whitespace or at line
   start → rest of line removed.
4. Continuation: a line whose first non-blank character is `+` is appended (with a space) to the
   previous logical line.
5. Outside braces/quotes: `(`, `)`, `,` → space; `=` surrounded by optional spaces is normalised to
   `=` with no spaces (`C = 1n` → `C=1n`); `PARAMS :` → `PARAMS:`.
6. Tokens split on whitespace, keeping `{…}` and `'…'` groups intact.
7. Case-insensitive for keywords, element names, subckt names, parameter names; node names are also
   case-insensitive (lower-cased). The first line is **not** treated as a title (unlike full SPICE
   decks), because vendor .mod files usually start with `*` comments or `.SUBCKT`.

**EBNF (logical lines):**

```
file        = { line } ;
line        = subckt_start | subckt_end | param_line | element | ignored | unsupported ;
subckt_start= ".SUBCKT" NAME node node { node } [ "PARAMS:" ] { assign } ;
subckt_end  = ".ENDS" [ NAME ] ;
param_line  = ".PARAM" assign { assign } ;
assign      = NAME "=" value ;
element     = r_elem | l_elem | c_elem | k_elem | x_elem ;
r_elem      = "R" SUFFIX node node ( value | "R=" value ) { opt } ;
l_elem      = "L" SUFFIX node node ( value | "L=" value ) { opt } ;
c_elem      = "C" SUFFIX node node ( value | "C=" value ) { opt } ;
k_elem      = "K" SUFFIX lname lname { lname } value ;     (all pairs coupled with same k)
x_elem      = "X" SUFFIX node { node } NAME [ "PARAMS:" ] { assign } ;
opt         = NAME "=" value ;                              (IC=, TC=, TC1=, TC2=, M=, …)
value       = number | "{" expr "}" | "'" expr "'" | NAME ;  (bare NAME = parameter reference)
ignored     = ".END" | ".MODEL" … | ".OPTIONS" … | ".TEMP" … | ".AC" … | ".TRAN" … ;
unsupported = ".INCLUDE" … | ".LIB" … | ".FUNC" … | element starting with V I E F G H B D Q M J T S W U O ;
```

Notes: in `x_elem` the subckt NAME is the last token before `PARAMS:` or before the first
`assign`. Options: `M=` (multiplier) on R/L/C is **supported** (R/M, L/M, C·M); all other options are
ignored with `W_SPICE_OPTION_IGNORED`. `.MODEL` etc. → `W_SPICE_IGNORED`. Unsupported → error
`E_SPICE_UNSUPPORTED` with file, line number and text.

**Numbers:** regex (case-insensitive) `^[+-]?(\d+\.?\d*|\.\d+)(e[+-]?\d+)?` followed by an optional
suffix, then any trailing letters which are ignored. Suffix matching order (longest first):

| Suffix | Multiplier |
|---|---|
| `MEG` | 1e6 |
| `MIL` | 25.4e-6 |
| `T` | 1e12 |
| `G` | 1e9 |
| `K` | 1e3 |
| `M` | 1e-3 |
| `U`, `µ`, `μ` | 1e-6 |
| `N` | 1e-9 |
| `P` | 1e-12 |
| `F` | 1e-15 |

Examples: `10u`→1e-5, `10uF`→1e-5, `1MEG`→1e6, `1Meg`→1e6, `2.2nH`→2.2e-9, `5mOhm`→5e-3,
`1e-9`→1e-9, `3mil`→7.62e-5, `10Ohm`→10 (O is not a suffix), `1F`→**1e-15** (SPICE semantics;
Help must warn about this), `1Farad`→1e-15, `0.1`→0.1. Note the exponent is parsed before the
suffix (`1e3k` → 1e6).

**Expressions** (inside `{}` or `''`, or on the right side of `.PARAM`): own recursive-descent
parser (MUST NOT use Python `eval`). Grammar:

```
expr   = term { ("+" | "-") term } ;
term   = unary { ("*" | "/") unary } ;
unary  = [ "+" | "-" ] power ;
power  = atom [ ("**" | "^") unary ] ;           (right associative)
atom   = number | NAME | NAME "(" expr { "," expr } ")" | "(" expr ")" ;
functions: sqrt, abs, exp, log (natural), log10, pow(x,y), min(x,y), max(x,y)
constants: pi
```

Division by zero or unknown name → `E_SPICE_EXPR` (file, line, expression).

**Parameter scoping and flattening:**

1. Global `.PARAM` (outside any subckt) evaluated in textual order → env_global.
2. Instantiating subckt S with instance X: env = env_parent (the caller's env, which for the top
   level is env_global) ∪ S.default_params (evaluated in env_parent) ∪ X.overrides (evaluated in
   the caller's env). Overrides of names not declared in S.default_params → warning
   `W_SPICE_UNKNOWN_PARAM` (still applied).
3. Local `.PARAM` lines inside S are evaluated in textual order in env and added to it; redefining a
   name that came from default/override params → error `E_SPICE_PARAM_REDEF`.
4. Element values are evaluated in the final env.
5. Nodes inside instance path `x1.x2` are renamed `x1.x2.<node>`; pins map to the caller's nodes;
   node `0` / `gnd` (global ground) inside any subckt → merged with the top-level pin2 and warning
   `W_SPICE_GLOBAL_GND`.
6. Element names become `x1.x2.<name>`; K references resolve within the same instance scope.
7. Recursion depth > 20 or a cycle → `E_SPICE_RECURSION`. Unknown subckt → `E_SPICE_UNKNOWN_SUBCKT`.
8. Nested `.SUBCKT` definitions inside a `.SUBCKT` are allowed and are global-scoped (definitions
   are collected in a first pass).

**Top-level subckt selection:** if the decap row specifies `subckt`, use it. Otherwise, candidates
= subckts not instantiated by any other subckt in the file; if exactly one → use it; if several →
the first (textual order) with exactly 2 pins, warning `W_SPICE_MULTI_TOP`. The chosen subckt MUST
have exactly 2 pins (pin1 = PWR side, pin2 = GND side), else `E_SPICE_PIN_COUNT`. A file without any
`.SUBCKT` → `E_SPICE_NO_SUBCKT`.

**Bundled example `examples/cap_0402_100nF.mod`:**

```spice
* Simple PI Calculator example model (synthetic, not vendor data)
* 0.1 uF 0402 X7R MLCC, series R-L-C
.SUBCKT CAP_0402_100NF 1 2
R1 1 11 30m
L1 11 12 0.45nH
C1 12 2 100nF
.ENDS CAP_0402_100NF
```

**Bundled example `examples/cap_0603_10uF.mod`** (nested subckt with parameters, continuation,
inline comment, `.PARAM`, coupled inductors):

```spice
* Simple PI Calculator example model (synthetic, not vendor data)
* 10 uF 0603 X5R MLCC with nested ESL/ESR branch
.PARAM CNOM=10u
.SUBCKT CAP_0603_10UF PIN1 PIN2 PARAMS: CDER=0.9
X_ESL PIN1 N1 RL_BRANCH PARAMS: LS=0.5n
+ RS=3m                                  ; continuation line
C1 N1 N2 {CNOM*CDER/0.9}                 $ nominal 10 uF
R2 N2 PIN2 2m
RLEAK N1 PIN2 100MEG
.ENDS CAP_0603_10UF

.SUBCKT RL_BRANCH A B PARAMS: LS=1n RS=1m
L1 A M1 {LS/2}
L2 M1 M2 {LS/2}
K12 L1 L2 0.0
R1 M2 B {RS}
.ENDS RL_BRANCH
```

(K12 = 0 keeps the total ESL at exactly 0.5 nH while exercising the K stamp; unit tests use non-zero
k separately.)

`examples/cap_0402_100nF_series.s2p` is a synthetic series-through file generated by
`tools/make_examples.py` from the 0402 model (Z0 = 50 Ω, 1 kHz–3 GHz, 201 log points, `# HZ S RI R 50`).

### 4.6 Touchstone v1 (.s2p) reader

* `!` starts a comment (anywhere on a line).
* Option line `# [freq_unit] [param] [format] [R z0]`, tokens in any order, case-insensitive;
  defaults `GHZ S MA R 50`. freq_unit ∈ {HZ, KHZ, MHZ, GHZ}; param must be `S` (else
  `E_S2P_PARAM`); format ∈ {MA (mag, angle deg), DB (20·log10 mag, angle deg), RI (real, imag)}.
  Only the first option line counts.
* A line starting with `[` (Touchstone 2.0 keyword such as `[Version]`) → `E_S2P_V2`.
* All numeric tokens after the option line are concatenated and grouped in records of 9:
  `f N11a N11b N21a N21b N12a N12b N22a N22b` (**v1 2-port order is 11, 21, 12, 22** [Touchstone09]).
* Frequencies must be strictly increasing; the first non-increasing frequency starts a noise-parameter
  block → stop reading (`W_S2P_NOISE_IGNORED`). Token count not multiple of 9 → `E_S2P_FORMAT`.
* At least 2 frequency points required.

### 4.7 Project file (.spical.json) and auto-save file

UTF-8 JSON, 2-space indent, keys in the order below. Two kinds of documents share **one schema**:

* **Named project file** (`<name>.spical.json`, File ▸ Save/Save As): inputs only, no `session`
  block. Paths are stored **relative to the project file** when on the same drive, else absolute,
  with forward slashes.
* **Auto-save file** (`autosave.spical.json`, §5.8): the same inputs plus a `session` block with
  window/plot state. All paths absolute (forward slashes).

Tables are embedded (so a project still opens if an Excel file moves); source paths are kept for
"Re-import". Versioning and migration: §5.8.4.

JSON Schema summary (draft 2020-12 semantics; implement validation by hand in
`io/project_io.py`):

| Key | Type | Notes |
|---|---|---|
| `format` | const `"simple-pi-calculator-project"` | |
| `schema_version` | int ≥ 1 | current = 4 (`CURRENT_SCHEMA_VERSION`); 3 = no distance distribution (always fixed); 2 = one PAD per net (no `pwr.rows[].n_pads`); 1 = v0.1 with `vias.vias_per_decap` |
| `app_version` | str | writer version |
| `stackup.source_path` | str or null | |
| `stackup.layers[]` | objects `{number:int, name:str, thickness_mm:float, conductivity_s_per_m:float or null, dk:float or null, df:float or null}` | |
| `vias.drill_diameter_mm` | float > 0 | default 0.2 |
| `vias.antipad_diameter_mm` | float > drill | default 0.5 |
| `vias.via_pitch_mm` | float > drill | default 1.0 (PWR–GND via centre spacing s_v) |
| `vias.vias_per_pad` | int ≥ 1 | default 1; parallel vias on **each** decap pad (n_pad PWR + n_pad GND vias per decap via set, §2.6.4). Schema 1 `vias_per_decap` v → `max(1, ceil(v/2))` |
| `vias.pad_via_count` | int ≥ 1 | default 1; PWR/GND via pairs **per** observation PAD (each of the N_pad pads of a net has this via set, §2.6.4) |
| `advanced.via_model` | `"pair"`/`"goldfarb_pucel"`/`"coax"` | default pair |
| `advanced.plating_thickness_mm` | float > 0 | default 0.025 |
| `advanced.via_conductivity_s_per_m` | float > 0 | default 5.8e7 |
| `advanced.mounting_inductance_nh` | float ≥ 0 | default 0 (per capacitor) |
| `advanced.s2p_default_mode` | `"series"`/`"shunt"` | default series |
| `advanced.model_search_dir` | str or null | |
| `advanced.workers` | int 0…256 | default 0 = auto (`os.cpu_count()`); compute worker threads (§3.9). Optional; out of range → `W_PROJECT_VALUE`, 0 used |
| `pwr.source_path` | str or null | |
| `pwr.rows[]` | `{name:str, pwr_layer:int, gnd_layer:int, width_mm:float, n_pads:int, enabled:bool}` | height is never stored (derived); `n_pads` = number of observation PADs N_pad (int ≥ 1, default 1 when missing; schema 2 → 3 adds `n_pads: 1`) |
| `decaps.source_path` | str or null | |
| `decaps.distance_mode` | `"fixed"`/`"normal"` | default fixed (§2.5.5); unknown value → `W_PROJECT_VALUE`, fixed used; schema 3 → 4 adds `"fixed"` |
| `decaps.sigma_mm` | float > 0 | default 0.5; σ of the normal distance distribution (absolute, mm); ≤ 0 → `W_PROJECT_VALUE`, 0.5 used |
| `decaps.seed` | int 0 … 2147483647 | default 12345; random seed; out of range → `W_PROJECT_VALUE`, 12345 used |
| `decaps.rows[]` | `{pwr_name:str, model_file:str, count:int, distance_mm:float, dummy:bool, subckt:str or null, s2p_mode:"series"/"shunt" or null, enabled:bool}` | `dummy` default false |
| `sweep` | `{f_start_hz, f_stop_hz, n_points, show_plane_only:bool}` | defaults 1e5, 1e9, 400, false |
| `display` | `{z_unit:"ohm"/"mohm"/"uohm"}` | default mohm |
| `session` | object, **auto-save file only** (ignored with info if found in a named file) | see below |

`session` object:

| Key | Type | Notes |
|---|---|---|
| `session.project_path` | str or null | absolute path of the named project currently associated with the state (null = untitled) |
| `session.modified` | bool | inputs differ from the named file (drives the `[*]` title marker) |
| `session.recent_files` | list[str], ≤ 8 | absolute paths, most recent first |
| `session.window.geometry_b64` | str | base64 of `QMainWindow.saveGeometry()` |
| `session.window.state_b64` | str | base64 of `QMainWindow.saveState()` (docks/toolbars) |
| `session.window.splitter_sizes` | list[int] | main splitter |
| `session.window.input_tab` | int | index of the input tab |
| `session.window.result_tab` | str or null | PWR name of the visible plot tab |
| `session.window.decap_filter` | str or null | decap table filter (PWR name, null = All) |
| `session.window.message_dock_visible` | bool | |
| `session.plots` | object PWR name → `{auto_range:bool, x_range_log10:[float,float], y_range_log10:[float,float]}` | y range stored for the current `display.z_unit` |
| `session.had_results` | bool | results were shown when saved (triggers recompute on restore) |
| `session.saved_utc` | str | ISO 8601 UTC timestamp |

Unknown keys are ignored with warning `W_PROJECT_UNKNOWN_KEY` (except inside `session`, where they
are silently ignored); missing optional keys take defaults.

Example named project (abridged):

```json
{
  "format": "simple-pi-calculator-project",
  "schema_version": 4,
  "app_version": "0.1.0",
  "stackup": {
    "source_path": "stackup_6L.xlsx",
    "layers": [
      {"number": 1, "name": "TOP", "thickness_mm": 0.035, "conductivity_s_per_m": 5.8e7, "dk": 4.2, "df": 0.02},
      {"number": 2, "name": "PP1", "thickness_mm": 0.1, "conductivity_s_per_m": null, "dk": 4.2, "df": 0.02}
    ]
  },
  "vias": {"drill_diameter_mm": 0.2, "antipad_diameter_mm": 0.5, "via_pitch_mm": 1.0,
           "vias_per_pad": 1, "pad_via_count": 1},
  "advanced": {"via_model": "pair", "plating_thickness_mm": 0.025, "via_conductivity_s_per_m": 5.8e7,
               "mounting_inductance_nh": 0.0, "s2p_default_mode": "series", "model_search_dir": null,
               "workers": 0},
  "pwr": {"source_path": "pwr_list.xlsx", "rows": [
    {"name": "VDD_CORE", "pwr_layer": 5, "gnd_layer": 3, "width_mm": 60, "n_pads": 1, "enabled": true},
    {"name": "VDD_IO", "pwr_layer": 7, "gnd_layer": 9, "width_mm": 30, "n_pads": 1, "enabled": true}]},
  "decaps": {"source_path": "decap_list.xlsx", "distance_mode": "fixed", "sigma_mm": 0.5,
             "seed": 12345, "rows": [
    {"pwr_name": "VDD_CORE", "model_file": "cap_0402_100nF.mod", "count": 10, "distance_mm": 8,
     "dummy": false, "subckt": null, "s2p_mode": null, "enabled": true},
    {"pwr_name": "VDD_IO", "model_file": "cap_0402_100nF.mod", "count": 4, "distance_mm": 5,
     "dummy": true, "subckt": null, "s2p_mode": null, "enabled": true}]},
  "sweep": {"f_start_hz": 1e5, "f_stop_hz": 1e9, "n_points": 400, "show_plane_only": false},
  "display": {"z_unit": "mohm"}
}
```

Auto-save file = the same document with absolute paths and, appended:

```json
  "session": {
    "project_path": "C:/Users/alice/Documents/board_a.spical.json",
    "modified": true,
    "recent_files": ["C:/Users/alice/Documents/board_a.spical.json"],
    "window": {"geometry_b64": "AdnQywADAAAAAA…", "state_b64": "AAAA/wAAAAD9…",
               "splitter_sizes": [440, 660], "input_tab": 3, "result_tab": "VDD_CORE",
               "decap_filter": "VDD_CORE", "message_dock_visible": true},
    "plots": {"VDD_CORE": {"auto_range": false, "x_range_log10": [5.0, 9.0], "y_range_log10": [-0.2, 3.5]}},
    "had_results": true,
    "saved_utc": "2026-09-15T10:00:00Z"
  }
```

`examples/example_project.spical.json` bundles the full example (all 11 layers, 2 PWRs, 4 decap
rows as in §4.3/§4.4).

### 4.8 Results export

Entry points: File ▸ Export ▸ (Results CSV…, Results XLSX…, Touchstone…, Plot PNG…, All Plots…)
and the same menu on the "↧ Export" button of the results toolbar. Every export reports an
`I_EXPORT` Info line with the written path(s) to the Messages dock (category `export`); any
exception becomes an `E_EXPORT` Error line — exports never raise into the GUI. PWR nets whose computation failed have no results and are skipped by every export; the export then adds a `W_EXPORT_SKIPPED` Warning naming them (review v0.2). A failed net always carries an Error with `source = "PWR:<name>"`: when its errors came from a file (model parse/read errors), `compute_project` adds `E_PWR_FAILED` (source `PWR:<name>`, location = that file) so the GUI can mark the net's tab as failed.

**CSV** (`io/export.py`), options dialog:

* *One file* (default, `export_csv_combined`): `Frequency (Hz)`, then per PWR
  `<PWR> |Z| (Ohm)`, `<PWR> Re Z (Ohm)`, `<PWR> Im Z (Ohm)` (+ `<PWR> |Z| plane only (Ohm)` if
  present). All PWRs must share one frequency grid (`ValueError` otherwise).
* *One file per PWR* (`export_csv`): `<project>_<PWR>.csv` with `Frequency (Hz)`, `Re Z (Ohm)`,
  `Im Z (Ohm)`, `|Z| (Ohm)`, and if plane-only enabled `|Z| plane only (Ohm)`.

A `#` header block lists version, project, PWR, `Number of PADs: N` (combined file:
`# <PWR>: Number of PADs = N`), UTC date, marker readouts and (per-PWR files) the `info` entries,
which include `n_pads`. Numbers `%.9e`.

**XLSX**: one workbook, sheet `Summary` (inputs digest, marker readouts, info) plus one sheet per
PWR (sheet name = PWR name truncated to 31 chars, invalid chars `[]:*?/\` replaced by `_`), same
columns as the per-PWR CSV.

**Touchstone v1** (writer `core/touchstone.format_touchstone_v1` / `write_touchstone_v1`, spec:
Touchstone 1.1, EIA/IBIS 2002):

* Layout: per-PWR 1-port `<project>_<PWR>.s1p` (`export_touchstone_per_pwr`), or one file with
  N = number of PWRs as **uncoupled** ports (`export_touchstone_combined`; off-diagonal = 0,
  extension `.sNp`, N ≤ 99; an existing `.sKp` extension is replaced, others get `.sNp` appended).
* Options (`TouchstoneOptions`): parameter `S` (default) or `Z`; format `RI` (default) or `MA`
  (angle in degrees); frequency unit always `Hz`; reference `R = 1 Ω` default (PDN convention),
  editable. `S11 = (Z − R)/(Z + R)`; `Z` data are normalised to R (v1 rule for Z/Y).
* Option line `# Hz S RI R 1`; `!` comments: tool name/version, project, UTC date, quantity and
  conversion, uncoupled-ports caveat (combined), per port: PWR name, `Number of PADs: N (joined at
  an ideal common node)`, geometry summary (PWR/GND layers, width, number of PADs, decaps, vias with
  "via pair(s) per observation PAD") and `info` (incl. `n_pads`).
* Data lines: `%.16e`; 1-port `f N11`; 2-port `f N11 N21 N12 N22`; N ≥ 3 row-major, every matrix
  row starts a new line (frequency only on the first), at most 4 pairs per line (rows wrap for
  N ≥ 5).

**Plot images**: *Plot PNG…* = `ImageExporter` of the visible plot, 1600 px wide, current view.
*All Plots…* (folder, PNG/SVG, width × height px, "keep current zoom") writes `All_PWRs.<ext>` and
one `<PWR>.<ext>` per PWR tab (`safe_file_name`: `<>:"/\|?*` and control chars → `_`, reserved
Windows names prefixed with `_`, case-insensitive duplicates get `_2`, `_3`, …), with the current
unit / marker / plane-only / curve-visibility settings, in the default view (§5.6) unless "keep
current zoom". Rendering details: §5.6.

**Distance distribution line (§2.5.5).** CSV (per-PWR header and combined file), Touchstone comments and
the XLSX Summary sheet contain one line per PWR: `Distance distribution: fixed (every capacitor at its row
distance)` or `Distance distribution: normal truncated to +/-1 sigma, sigma = <σ> mm, seed = <seed>;
sampled min/mean/max = <a>/<b>/<c> mm over <P> via set(s)` (ASCII, `io.export.distance_summary_line`).

---

## 5. Software architecture

### 5.1 Repository layout

```
simple-pi-calculator/
├─ LICENSE                         MIT, "Copyright (c) 2026 Simple PI Calculator contributors"
├─ README.md
├─ pyproject.toml
├─ src/simple_pi_calculator/
│  ├─ __init__.py                  __version__ = "0.1.0"
│  ├─ __main__.py                  python -m simple_pi_calculator
│  ├─ app.py                       QApplication bootstrap
│  ├─ constants.py                 MU0, EPS0, C0, defaults, marker freqs
│  ├─ errors.py                    Issue, Severity, InputError, collectors
│  ├─ core/
│  │  ├─ __init__.py
│  │  ├─ units.py                  mm↔m, unit display scaling
│  │  ├─ stackup.py                Layer, Stackup, PlanePair derivation
│  │  ├─ cavity.py                 CavityModel (modal Z matrix), CavityCache
│  │  ├─ parallel.py               worker threads, chunking, BLAS thread policy (§3.9)
│  │  ├─ placement.py              derived plane height, axial row placement, dummy-cap ports
│  │  ├─ via.py                    via loop impedance
│  │  ├─ spice_expr.py             number & expression parser
│  │  ├─ spice_parser.py           .mod parser + flattener
│  │  ├─ mna.py                    AC MNA solver
│  │  ├─ touchstone.py             s2p reader + S→Z + interpolation
│  │  ├─ decap_model.py            DecapModel protocol, cache
│  │  ├─ pdn.py                    compute_pwr(), Schur reduction
│  │  └─ engine.py                 compute_project(), progress/cancel
│  ├─ io/
│  │  ├─ __init__.py
│  │  ├─ excel_headers.py          normalisation + fuzzy rules
│  │  ├─ excel_import.py           read_stackup / read_pwr_list / read_decap_list
│  │  ├─ project_io.py             load/save .spical.json, AutosaveStore (Qt-free)
│  │  ├─ migrations.py             schema_version migration chain
│  │  └─ export.py                 CSV/XLSX export
│  ├─ gui/
│  │  ├─ __init__.py
│  │  ├─ main_window.py
│  │  ├─ models.py                 QAbstractTableModel subclasses
│  │  ├─ delegates.py              file-browse, combo delegates
│  │  ├─ panels.py                 StackupPanel, ViaPanel, PwrPanel, DecapPanel, SweepPanel
│  │  ├─ plot_widget.py            ImpedancePlot (pyqtgraph)
│  │  ├─ worker.py                 ComputeWorker (QObject in QThread)
│  │  ├─ persistence.py            AutosaveManager (QTimer debounce, QLockFile, session state)
│  │  ├─ placement_preview.py      PlacementPreview widget (plane W×H, PAD row, ports)
│  │  ├─ message_dock.py
│  │  └─ help_window.py
│  ├─ help/                        *.html, style notes, img/*.png
│  └─ resources/                   app.ico, app.png, svg sources
├─ tests/
│  ├─ conftest.py
│  ├─ data/                        fixtures (.mod, .s2p, .xlsx generated in conftest)
│  └─ test_*.py
├─ examples/                       stackup_6L.xlsx, pwr_list.xlsx, decap_list.xlsx,
│                                  cap_0402_100nF.mod, cap_0603_10uF.mod,
│                                  cap_0402_100nF_series.s2p, example_project.spical.json
├─ tools/
│  ├─ bench.py                     performance benchmark scenarios (§3.9)
│  ├─ make_examples.py             regenerates example xlsx/s2p deterministically
│  └─ render_diagrams.py           SVG → PNG using QtSvg (no extra deps)
├─ docs/DESIGN.md, docs/diagrams/*.svg
├─ packaging/
│  ├─ simple_pi_calculator.spec
│  ├─ installer.iss
│  └─ version_info.txt             generated in CI
└─ .github/workflows/build-windows.yml
```

`pyproject.toml`: build backend setuptools ≥ 68, `requires-python = ">=3.11,<3.12"`,
dependencies `PySide6>=6.6,<6.9`, `pyqtgraph>=0.13.4`, `numpy>=1.26,<3`, `openpyxl>=3.1`,
`scipy>=1.11` (optional extra `[full]`; core must not import it). Dev extra: `pytest`,
`pytest-qt`, `pyinstaller>=6.3`. Entry point `[project.gui-scripts] simple-pi-calculator =
"simple_pi_calculator.app:main"`. Package data: `help/**/*`, `resources/**/*`.

The `core` and `io` packages MUST NOT import Qt (headless testable).

### 5.2 Core data classes (`core`, `errors`)

```python
# errors.py
class Severity(enum.Enum): INFO = 0; WARNING = 1; ERROR = 2

@dataclass(frozen=True)
class Issue:
    code: str                  # e.g. "E_STACK_DK"
    severity: Severity
    message: str               # human readable, English
    source: str | None = None  # file path, "PWR:VDD_CORE", etc.
    location: str | None = None  # "Stackup!C7", "line 12"

class InputError(Exception):
    def __init__(self, issues: list[Issue]): ...

class IssueCollector:
    def add(self, code: str, severity: Severity, message: str, source=None, location=None) -> None: ...
    def has_errors(self) -> bool: ...
    def raise_if_errors(self) -> None: ...
    issues: list[Issue]
```

```python
# core/stackup.py
@dataclass(frozen=True)
class Layer:
    number: int
    name: str
    thickness_m: float
    conductivity: float | None     # S/m; None or 0 → dielectric
    dk: float | None
    df: float | None
    @property
    def is_metal(self) -> bool: ...

@dataclass(frozen=True)
class Stackup:
    layers: tuple[Layer, ...]      # sorted by number
    def by_number(self, n: int) -> Layer: ...
    def z_top(self, n: int) -> float: ...
    def z_center(self, n: int) -> float: ...
    @property
    def total_thickness(self) -> float: ...
    def validate(self, issues: IssueCollector) -> None: ...

@dataclass(frozen=True)
class PlanePair:
    pwr_layer: Layer
    gnd_layer: Layer
    d_m: float
    er_eff: float
    tand_eff: float
    def plane_capacitance(self, a_m: float, b_m: float) -> float: ...

def derive_plane_pair(stackup: Stackup, pwr_layer: int, gnd_layer: int,
                      issues: IssueCollector, source: str) -> PlanePair: ...
```

```python
# core/cavity.py
def surface_impedance(f_hz: np.ndarray, sigma: float, t_m: float) -> np.ndarray: ...
def conductor_loss_factor(f_hz: np.ndarray, pair: PlanePair) -> np.ndarray: ...   # Γ_c
def wavenumber_sq(f_hz: np.ndarray, pair: PlanePair) -> np.ndarray: ...           # k²

@dataclass(frozen=True)
class ModeSettings:
    k_split_factor: float = 4.0
    port_factor: float = 3.0
    min_modes: int = 16
    max_modes_per_axis: int = 1500

class CavityModel:
    def __init__(self, a_m: float, b_m: float, pair: PlanePair,
                 port_xy_m: np.ndarray,          # (P,2)
                 port_widths_m: np.ndarray,      # (P,) per-port w_p
                 f_eval_hz: np.ndarray,          # all evaluation frequencies (for k_max, §3.2)
                 settings: ModeSettings = ModeSettings(),
                 progress: Callable[[float], None] | None = None,
                 cancel: Callable[[], bool] | None = None): ...
    M: int; N: int; n_dynamic: int; capped: bool
    def z_matrix(self, f_hz: np.ndarray) -> np.ndarray: ...   # (F,P,P) complex128

def cluster_via_positions(n: int, pitch_m: float) -> np.ndarray: ...   # (n,2), row-major, cols=ceil(√n), centred
def cluster_port_width(n_vias: int, drill_diameter_m: float, via_pitch_m: float) -> float: ...
    # w_p = g_p/0.44705 with grid pitch √2·s_v (§2.4.5); n=1 → 1.11845·D
def resonance_frequency(m: int, n: int, a_m: float, b_m: float, er: float) -> float: ...
```

```python
# core/placement.py
@dataclass(frozen=True)
class DecapGroupGeom:
    count: int                 # N_k ≥ 1
    distance_m: float          # d_k > 0
    dummy: bool = False        # δ_k
    port_distances_m: tuple[float, ...] | None = None   # d_kj per port (§2.5.5); None = d_k

@dataclass(frozen=True)
class Placement:
    width_m: float             # W
    height_m: float            # H = 1.4·D_ref
    d_ref_m: float
    xy_m: np.ndarray           # (P,2), rows 0 … N_pad−1 = PADs, then port order of §2.5.3
    group_index: np.ndarray    # (P-N_pad,) int, row k of each decap port
    caps_per_port: np.ndarray  # (P-N_pad,) int ∈ {1,2}
    port_widths_m: np.ndarray  # (P,) w_pad × N_pad, then w_dec
    n_pads: int = 1            # N_pad; properties n_ports, n_decap_ports = P − N_pad,
                               # pad_xy_m (first pad), pads_xy_m (N_pad, 2)
    port_distances_m: np.ndarray | None  # (P-N_pad,) d_kj (= d_k in fixed mode, §2.5.5)

def plane_height(width_m: float, distances_m: Sequence[float]) -> tuple[float, float]:
    """Returns (H, D_ref) per §2.5.1 (no distances → D_ref = W/1.4, H = W)."""
def ports_for_row(count: int, dummy: bool) -> int: ...                 # P_k
def caps_per_port_for_row(count: int, dummy: bool) -> list[int]: ...   # c_{k,j}, len P_k
def place_ports(width_m: float, groups: Sequence[DecapGroupGeom],
                decap_port_width_m: float, pad_port_width_m: float,
                issues: IssueCollector, source: str, n_pads: int = 1) -> Placement: ...
    # PAD row of §2.5.1 (E_PWR_NPADS, W_PAD_CLIPPED), then the decap rows of §2.5.3
```

```python
# core/via.py
@dataclass(frozen=True)
class ViaSettings:
    drill_diameter_m: float
    antipad_diameter_m: float
    via_pitch_m: float = 1.0e-3               # s_v, PWR–GND via centre spacing
    vias_per_pad: int = 1                     # n_pad: parallel vias on EACH decap pad
    pad_via_count: int = 1                    # PWR/GND via pairs per observation PAD
    model: Literal["pair", "goldfarb_pucel", "coax"] = "pair"
    plating_thickness_m: float = 25e-6
    conductivity: float = 5.8e7
    mounting_inductance_h: float = 0.0      # per capacitor, applied in pdn (§2.6.5)

@dataclass(frozen=True)
class ViaGeometry:
    nearer_layer: int
    h_near_m: float
    t_near_m: float
    h_r_m: float                              # 2·h_near + t_near

def via_geometry(stackup: Stackup, pwr_layer: int, gnd_layer: int,
                 issues: IssueCollector) -> ViaGeometry: ...                 # Top side, §2.6.1
def partial_mutual_inductance(length_m: float, distance_m: float) -> float: ...   # M_p
def pair_inductance(h_m: float, pitch_m: float, drill_d_m: float) -> float: ...   # L_pair
def antipad_inductance(t_m: float, drill_d_m: float, antipad_d_m: float) -> float: ...  # L_ap
def coax_inductance(length_m: float, drill_d_m: float, antipad_d_m: float) -> float: ...  # legacy
def goldfarb_pucel_inductance(length_m: float, drill_d_m: float) -> float: ...     # L_GP
def loop_inductance(geom: ViaGeometry, vs: ViaSettings) -> float: ...             # per model
def via_resistance(f_hz: np.ndarray, length_m: float, drill_d_m: float,
                   plating_t_m: float, sigma: float) -> np.ndarray: ...
def via_pair_impedance(f_hz: np.ndarray, stackup: Stackup, pwr_layer: int, gnd_layer: int,
                       vs: ViaSettings) -> np.ndarray: ...
def decap_via_impedance(f_hz, stackup, pwr_layer, gnd_layer, vs) -> np.ndarray: ...  # / n_pair_dec (= vias_per_pad)
def pad_via_impedance(f_hz, stackup, pwr_layer, gnd_layer, vs) -> np.ndarray: ...    # / n_pad
```

```python
# core/spice_expr.py
def parse_spice_number(text: str) -> float: ...                 # raises ValueError
def evaluate_expression(expr: str, env: Mapping[str, float]) -> float: ...  # raises SpiceExprError

# core/spice_parser.py
@dataclass
class Element:
    kind: Literal["R", "L", "C", "K"]
    name: str
    nodes: tuple[str, ...]          # R/L/C: 2 nodes; K: ()
    value: float                    # R ohm, L H, C F, K coefficient
    coupled: tuple[str, ...] = ()   # K: inductor names
    line: int = 0

@dataclass
class SubcktDef:
    name: str
    pins: list[str]
    default_params: list[tuple[str, str]]     # unevaluated
    body: list[tuple[int, list[str]]]         # (line_no, tokens)

@dataclass
class Netlist:
    elements: list[Element]
    pin1: str
    pin2: str
    subckt_name: str
    source_path: str

def parse_spice_file(path: str | os.PathLike, subckt: str | None,
                     issues: IssueCollector) -> Netlist: ...
def parse_spice_text(text: str, subckt: str | None, issues: IssueCollector,
                     source_path: str = "<text>") -> Netlist: ...
```

```python
# core/mna.py
def impedance_two_terminal(netlist: Netlist, f_hz: np.ndarray, gmin: float = 1e-12) -> np.ndarray:
    """Z(f) between pin1 and pin2, complex128, shape (F,)."""

# core/touchstone.py
@dataclass
class TwoPortData:
    f_hz: np.ndarray        # (F0,)
    s: np.ndarray           # (F0,2,2) complex; s[:,1,0] = S21
    z0: float

def read_s2p(path, issues: IssueCollector) -> TwoPortData: ...
def s2p_to_impedance(data: TwoPortData, mode: Literal["series", "shunt"],
                     issues: IssueCollector) -> np.ndarray: ...            # at data.f_hz
def interpolate_impedance(f_src: np.ndarray, z_src: np.ndarray, f_dst: np.ndarray,
                          issues: IssueCollector, source: str) -> np.ndarray: ...

# core/decap_model.py
class DecapModel(Protocol):
    label: str
    def impedance(self, f_hz: np.ndarray) -> np.ndarray: ...

class SpiceDecapModel:      # wraps Netlist + mna
class S2pDecapModel:        # wraps TwoPortData + mode

class DecapModelCache:
    def get(self, path: str, subckt: str | None, s2p_mode: str,
            issues: IssueCollector) -> DecapModel: ...   # key includes a content digest
```

```python
# core/pdn.py
@dataclass(frozen=True)
class PwrSpec:
    name: str
    pwr_layer: int
    gnd_layer: int
    width_m: float                           # height is derived (§2.5.1)
    n_pads: int = 1                          # N_pad observation pads (§2.5.1, §2.8)

@dataclass(frozen=True)
class DecapGroup:
    pwr_name: str
    model: DecapModel
    count: int
    distance_m: float
    dummy: bool = False
    port_distances_m: tuple[float, ...] | None = None   # sampled d_kj (§2.5.5)

@dataclass
class PwrResult:
    name: str
    f_hz: np.ndarray               # plot grid (F,)
    z_pad: np.ndarray              # (F,) complex
    z_plane_only: np.ndarray | None
    marker_f_hz: np.ndarray        # (≤3,)
    marker_z: np.ndarray           # complex, exact
    placement: Placement             # derived W, H, D_ref, port coordinates (for preview/export)
    info: dict[str, float | int | str]   # C_plane, er_eff, tand_eff, d_m, W_m, H_m, D_ref_m, M, N, n_dynamic, P, n_pads, h_near_m, h_r_m, L_loop, w_pad_m, w_dec_m, min_rcond
    issues: list[Issue]
    distance: DistanceDistribution | None = None        # §2.5.5; None = fixed
    sampled_distances: list[tuple[int, int, float]]     # (row k, port j in row, d_kj mm) per decap port
    n_pads: int  (property)          # = placement.n_pads

def compute_pwr(stackup: Stackup, pwr: PwrSpec, groups: Sequence[DecapGroup],
                vias: ViaSettings, f_grid_hz: np.ndarray, marker_f_hz: Sequence[float],
                want_plane_only: bool, issues: IssueCollector,
                progress: Callable[[float], None] | None = None,
                cancel: Callable[[], bool] | None = None,
                settings: ModeSettings = ModeSettings(), workers: int | None = 1,
                cavity_cache: CavityCache | None = None,
                distance: DistanceDistribution | None = None) -> PwrResult: ...
    # normal mode: groups without port_distances_m are sampled with one generator over `groups`

def port_loads(f_hz: np.ndarray, placement: Placement, groups: Sequence[DecapGroup],
               z_decap: Sequence[np.ndarray], z_via_dec: np.ndarray,
               mounting_inductance_h: float) -> np.ndarray:
    """(F, P-N_pad) complex: Z_L,p = (Z_decap,k + jωL_mount)/c_p + Z_via,dec (§2.6.5)."""

def reduce_ports(z_cav: np.ndarray, z_load: np.ndarray, n_pads: int = 1) -> np.ndarray:
    """z_cav (F,P,P), z_load (F,P-N_pad) → Z_red: (F,) for N_pad = 1, else (F,N_pad,N_pad)."""

def combine_pads(z_pp_red: np.ndarray, z_via_pad: np.ndarray) -> np.ndarray:
    """(F,N,N), (F,) → (F,) Z_PAD = 1/(1ᵀ(Z_pp,red + Z_via,pad·I)⁻¹1) (§2.8)."""
```

```python
# core/engine.py
@dataclass
class ProjectInputs:           # pure-python mirror of the project JSON, SI-converted
    stackup: Stackup
    vias: ViaSettings
    pwrs: list[PwrSpec]
    decap_rows: list[DecapRow]       # path, subckt, s2p_mode, count, distance_m, dummy, pwr_name, enabled
    f_start_hz: float
    f_stop_hz: float
    n_points: int
    show_plane_only: bool
    project_dir: str | None
    model_search_dir: str | None
    s2p_default_mode: str
    distance: DistanceDistribution = DistanceDistribution()   # §2.5.5 (mode, sigma_m, seed)

class CancelledError(Exception): ...
def sample_project_distances(rows: Sequence[DecapRow], distance: DistanceDistribution | None
                             ) -> dict[int, tuple[float, ...]]: ...
    # §2.5.5: {table row index: d_kj per port}, one generator over the enabled rows; {} for fixed

# core/distribution.py (§2.5.5)
@dataclass(frozen=True)
class DistanceDistribution:
    mode: str = "fixed"; sigma_m: float = 0.5e-3; seed: int = 12345
    is_fixed: bool (property); def validate(self) -> list[tuple[str, str]]: ...
def norm_cdf(x) -> np.ndarray: ...                 # ½·erfc(−x/√2)
def norm_ppf(p) -> np.ndarray: ...                 # Acklam + one Halley step
def truncated_standard_normal(rng, n) -> np.ndarray: ...
def sample_offsets(port_counts, seed) -> list[np.ndarray]: ...
def sample_row_distances(rows: Sequence[tuple[int, float, bool]],
                         distribution) -> list[tuple[float, ...] | None]: ...

def validate_inputs(inputs: ProjectInputs) -> list[Issue]: ...
def compute_project(inputs: ProjectInputs,
                    progress: Callable[[float, str], None] | None = None,
                    cancel: Callable[[], bool] | None = None
                    ) -> tuple[list[PwrResult], list[Issue]]:
    """Validates, computes every enabled PWR; per-PWR errors do not abort other PWRs."""
```

Progress weights: per PWR 10 % decap models, 60 % static sums, 25 % dynamic sums, 5 % reduction.
Cancellation is polled once per m-row in the static sum loop and once per frequency chunk.

### 5.3 io API

```python
# io/excel_headers.py
@dataclass(frozen=True)
class ColumnRule:
    field: str
    required: bool
    predicate: Callable[[str, str | None], bool]   # (base, unit) → match
    kind: Literal["int", "float", "length", "str", "mode"]

def normalize_header(text: object) -> tuple[str, str | None]: ...  # (base, unit)
def match_columns(header_cells: Sequence[object], rules: Sequence[ColumnRule],
                  issues: IssueCollector, sheet: str, row: int) -> dict[str, tuple[int, str | None]]: ...
STACKUP_RULES: tuple[ColumnRule, ...]
PWR_RULES: tuple[ColumnRule, ...]
DECAP_RULES: tuple[ColumnRule, ...]

# io/excel_import.py
def read_stackup(path, issues) -> Stackup: ...
def read_pwr_list(path, issues) -> list[PwrRow]: ...        # mm-valued rows for the GUI tables (incl. n_pads)
def read_decap_list(path, issues) -> list[DecapRow]: ...

# io/project_io.py
@dataclass
class Project: ...   # mm-valued, JSON-mirroring dataclass (see §4.7), with defaults;
                     # Project.distance: DistanceSettings(mode="fixed", sigma_mm=0.5, seed=12345)

@dataclass
class WindowState:
    geometry_b64: str = ""; state_b64: str = ""; splitter_sizes: list[int] = field(default_factory=list)
    input_tab: int = 0; result_tab: str | None = None; decap_filter: str | None = None
    message_dock_visible: bool = True

@dataclass
class PlotView:
    auto_range: bool = True
    x_range_log10: tuple[float, float] | None = None
    y_range_log10: tuple[float, float] | None = None

@dataclass
class Session:
    project_path: str | None = None
    modified: bool = False
    recent_files: list[str] = field(default_factory=list)
    window: WindowState = field(default_factory=WindowState)
    plots: dict[str, PlotView] = field(default_factory=dict)
    had_results: bool = False
    saved_utc: str | None = None

class ProjectFormatError(Exception): ...        # not JSON / wrong format / structural type errors
class ProjectTooNewError(Exception):
    schema_version: int

def project_to_dict(project: Project, anchor_dir: str | None, session: Session | None) -> dict: ...
def project_from_dict(doc: dict, anchor_dir: str | None, issues: IssueCollector
                      ) -> tuple[Project, Session | None]: ...     # runs migrations first
def load_project(path) -> tuple[Project, list[Issue]]: ...          # session ignored
def save_project(project: Project, path) -> None: ...               # no session; atomic write
def write_json_atomic(path: str, doc: dict, backup_path: str | None = None) -> bool: ...
def to_inputs(project: Project, project_path: str | None) -> ProjectInputs: ...

@dataclass
class AutosaveLoadResult:
    project: Project
    session: Session
    source: Literal["primary", "backup", "defaults"]
    issues: list[Issue]

class AutosaveStore:                      # Qt-free, unit-testable
    FILE = "autosave.spical.json"
    BACKUP = "autosave.bak.spical.json"
    def __init__(self, directory: str): ...
    @property
    def path(self) -> str: ...
    def load(self) -> AutosaveLoadResult: ...                     # §5.8.3 recovery rules
    def save(self, project: Project, session: Session) -> bool: ...   # False if skipped (unchanged)
    def quarantine(self, path: str, tag: str) -> str: ...        # rename, returns new path

# io/migrations.py
CURRENT_SCHEMA_VERSION: int = 4
def migrate_1_to_2(doc: dict) -> dict: ...           # vias.vias_per_decap v → vias.vias_per_pad
def migrate_2_to_3(doc: dict) -> dict: ...           # pwr.rows[].n_pads = 1 added
def migrate_3_to_4(doc: dict) -> dict: ...           # decaps.distance_mode/sigma_mm/seed = fixed/0.5/12345
MIGRATIONS: dict[int, Callable[[dict], dict]] = {1: migrate_1_to_2, 2: migrate_2_to_3, 3: migrate_3_to_4}
def migrate(doc: dict, issues: IssueCollector) -> dict: ...

# io/export.py
def export_csv(results: Sequence[PwrResult], folder: str, stem: str) -> list[str]: ...
def export_xlsx(results: Sequence[PwrResult], path: str, project: Project) -> None: ...
```

### 5.4 Threading (compute)

* `gui/worker.py`: `class ComputeWorker(QObject)` with signals
  `progress = Signal(float, str)`, `finished = Signal(object)` (tuple(results, issues)),
  `failed = Signal(str)` (traceback text), and slot `run()`. It holds a deep copy of
  `ProjectInputs` taken on the GUI thread before start (the GUI may be edited during compute).
* Start sequence in `MainWindow.start_compute()`:
  1. `inputs = to_inputs(project)`; run `validate_inputs` synchronously; if errors → show in message
     dock, focus the offending panel, do not start.
  2. `thread = QThread(self)`; `worker.moveToThread(thread)`; `thread.started.connect(worker.run)`;
     `worker.finished.connect(self.on_compute_finished)`; `worker.finished.connect(thread.quit)`;
     `worker.failed.connect(...)`; `thread.finished.connect(worker.deleteLater)`;
     `thread.finished.connect(thread.deleteLater)`; `thread.start()`.
  3. Cancel: `worker.request_cancel()` sets a `threading.Event`; engine polls it via the `cancel`
     callback and raises `CancelledError`, which the worker reports as a cancellation message.
     Inside the worker thread the engine uses its own thread pools (§3.9); `progress` and `cancel`
     may therefore be called from pool threads (progress calls are serialised by the engine; the
     worker only emits queued signals).
* Progress signal emissions are throttled to ≥ 50 ms apart. The worker never touches widgets.
* While running: Compute action disabled, Cancel enabled, QProgressBar in the status bar.
* On close during compute: request cancel, `thread.wait(5000)`.
* After `on_compute_finished` the auto-save manager is flushed immediately (sets
  `session.had_results = True`). Auto-save serialisation always runs on the GUI thread.

### 5.5 GUI layout

`MainWindow(QMainWindow)`, title `Simple PI Calculator — <project name>[*]` (modified marker),
minimum 1100 × 700.

**Menus**

* File: New (Ctrl+N), Open… (Ctrl+O), Save (Ctrl+S), Save As… (Ctrl+Shift+S), Recent Files ▸
  (≤ 8 entries + "Clear Recent Files", stored in the auto-save `session`, not QSettings), Open Example
  Project, Import ▸ (Stack-up…, PWR List…, Decap List…), Export ▸ (Results CSV…, Results XLSX…,
  Plot PNG…), Show Auto-save Folder, Exit. Semantics: §5.8.5.
* Compute: Run (F5), Cancel (Esc while running).
* View: |Z| unit ▸ (Ω, mΩ, µΩ; exclusive QActionGroup), Show plane-only curve (checkable),
  Reset zoom (Ctrl+0), Show Messages dock.
* Help: Help Contents (F1), Open Help in Browser, Open Examples Folder, About, License.

**Central widget:** horizontal `QSplitter`:

* **Left (inputs, ~40 %)** `QTabWidget`:
  1. *Stack-up*: file path line-edit (read-only) + "Import…" + "Re-import"; `QTableView`
     (Layer #, Name, Type [Metal/Dielectric], Thickness mm, σ S/m, Dk, Df, z_top mm) read-only;
     below, a `StackupPreview` QWidget painting the cross-section (metal = copper colour, dielectric
     = light green, highlighted selected PWR/GND pair).
  2. *Vias* (`ViaPanel`): `QFormLayout` with `QDoubleSpinBox` drill diameter (0.01–5 mm, 3 decimals),
     anti-pad diameter, **Via pitch (PWR–GND via centre spacing, mm)** (0.02–20 mm, 3 decimals,
     default 1.000; tooltip explains that it sets the via-pair loop inductance and, for several
     pairs, the cluster size), `QSpinBox` **"Vias per decap pad"** (1–32, default 1; tooltip: number of
     parallel vias on each of the two decap pads, i.e. n PWR + n GND vias per decap via set, Z_via,dec =
     Z_viapair/n, wider cluster port), `QSpinBox` **"PAD vias (per observation pad)"** (1–400; tooltip: PWR/GND
     via pairs of each observation PAD, independent of the decap setting; total = # PADs × count), a read-only label
     "Decaps and PAD are mounted on the Top side"; derived read-only labels per selected PWR:
     h_near mm, L_loop nH, PAD / decap port width mm. Collapsible `QGroupBox` "Advanced" (via model, plating thickness, via
     conductivity, mounting inductance per capacitor nH, s2p default mode, model search folder).
  3. *PWR Nets*: toolbar (Import…, Add, Remove, Duplicate), editable `QTableView` with
     `PwrTableModel` (columns: Enabled ☑, PWR Name, PWR Layer, GND Layer, Width mm, **# PADs**
     [spin delegate 1–10000, header tooltip "Number of PADs: observation/contact pads …", error
     `E_PWR_NPADS` if < 1]; derived read-only: D_ref mm, Height mm, Ports (decap ports), d mm,
     εr_eff, C_plane pF). Derived columns update when the decap table changes. Cells with errors get
     red background and tooltip with the Issue message. Below the table a `PlacementPreview` widget
     (QPainter, no computation) draws the synthetic plane W × H of the selected PWR with all N_pad
     PADs (red squares, label "PAD" or "N PADs", header text "N_pad = N"), decap ports (blue squares;
     ports carrying 2 caps drawn with a double outline) and dimension labels, using
     `core.placement.place_ports(…, n_pads)`. With the `normal` distance distribution the ports are
     placed at the sampled distances of `engine.sample_project_distances` (same samples as the
     computation, §2.5.5) and the header adds "normal ±1σ: σ … mm, seed …"; a tooltip on
     hover names the port under the cursor (PAD i, or decap row with table row number and model file,
     via set index, capacitors, distance to the PAD row, x/y).
  4. *Decaps*: filter `QComboBox` ("All PWRs" + names; auto-synced to the selected row in the PWR
     tab); toolbar (Import…, Add, Remove); `DecapTableModel` via `QSortFilterProxyModel` (columns:
     Enabled, PWR Name [combo delegate], Decap File [line edit + "…" browse delegate], Subckt,
     S2P Mode, Count, Distance mm, Dummy Cap ☑ [checkbox, `Qt.ItemIsUserCheckable`], derived:
     Via sets (P_k), C @100 kHz, SRF MHz). Adding a row pre-fills PWR Name
     with the filter value. A "Preview model" button plots |Z_decap| in a small dialog. A second top
     bar holds the **global distance distribution** (§2.5.5): "Distance:" `QComboBox`
     [Fixed | Normal (±1σ)], "σ (mm)" `QDoubleSpinBox` (0.01–50, 3 decimals, default 0.5), "Seed"
     `QSpinBox` (0 … 2³¹−1, default 12345) and a "New seed" button (`secrets.randbelow`, always a
     different value); σ, seed and the button are disabled in Fixed mode. Any change writes
     `Project.distance`, emits `DecapPanel.edited` (→ modified, stale results, auto-save) and refreshes
     the placement preview.
  5. *Sweep*: f start / f stop (`QLineEdit` with an **engineering** frequency parser, NOT the SPICE
     parser: case-sensitive suffixes `k`/`K` = 1e3, `M` or `meg`/`MEG` = 1e6, `G` = 1e9, optional
     trailing `Hz`; lower-case `m` is rejected to avoid milli/mega confusion; e.g. `100k`, `1G`,
     `2.5MHz`), points spin box, "Show plane-only curve" check box.
* **Right (results, ~60 %)**: `QTabWidget` with one `ImpedancePlot` per computed PWR (tab text =
  PWR name, red icon if that PWR failed), plus a `QTableWidget` readout table below
  (rows = PWRs, columns = |Z| @ 1 MHz, 10 MHz, 100 MHz in the current unit, 4 significant digits).
* **Bottom dock** `MessageDock`: `QTreeWidget` (Severity icon, Code, Message, Source, Location);
  double-click navigates to the relevant tab/row.
* **Status bar**: progress bar, last-compute time, mode count info of current PWR.

Edits mark the project modified and schedule an auto-save (§5.8); results are marked "stale" (plot
title suffix "(inputs changed)") until recomputed. Run, Save and window close first commit pending
edits (`MainWindow.commit_pending_edits`): an open table-cell editor is committed and closed, and a
focused spin box / line edit that commits on Enter or focus-out is interpreted, because F5, Ctrl+S
and toolbar buttons do not take the keyboard focus. Check-box cells (Enabled, Dummy Cap) use
`CheckBoxDelegate`: one click anywhere in the cell toggles once, a double click toggles once, Space
toggles. Auto-compute is not performed on edits (only once
on restore, §5.8.3).

### 5.6 Plot widget (pyqtgraph specifics)

`class ImpedancePlot(pg.GraphicsLayoutWidget)`:

* `pg.setConfigOptions(antialias=True, background="w", foreground="k")` once at app start.
* `self.plot = self.addPlot()`; `self.plot.setLogMode(x=True, y=True)`;
  `self.plot.showGrid(x=True, y=True, alpha=0.3)`; labels: bottom "Frequency" (units shown as
  text "Hz" — call `getAxis('bottom').enableAutoSIPrefix(False)` and set tick strings via a custom
  `LogFreqAxis(pg.AxisItem)` overriding `tickStrings` to print `100k, 1M, 10M, 100M, 1G`); left
  "|Z| (mΩ)" with `enableAutoSIPrefix(False)`.
* **Log-mode coordinates:** in log mode pyqtgraph plots data given in linear units but the ViewBox
  coordinate system is log10. Therefore `InfiniteLine(pos=math.log10(f_marker), angle=90)`,
  `TextItem.setPos(log10(f), log10(value_scaled))`, and `setXRange/setYRange` use log10 values.
  This is a common pitfall and MUST be followed.
* Curves: `self.curve = self.plot.plot(f, abs_z_scaled, pen=pg.mkPen('#1f77b4', width=2),
  name="Z at PAD")`; plane-only `pen=pg.mkPen('#7f7f7f', width=1, style=Qt.DashLine)`.
  `self.plot.addLegend(offset=(-10, 10))`.
* Markers: for each marker frequency within range, `pg.InfiniteLine(angle=90, movable=False,
  pen=pg.mkPen('#d62728', width=1, style=Qt.DashLine), label='1 MHz',
  labelOpts={'position': 0.95, 'color': '#d62728'})`; a `pg.ScatterPlotItem` dot at the exact
  (f, |Z|) and a `pg.TextItem` with `"|Z| = 3.289 mΩ"` anchored (0, 1). Values come from
  `PwrResult.marker_z` (exact), formatted with 4 significant digits.
* **Unit switching:** scale factor s ∈ {1, 1e3, 1e6}; `setData(f, max(|Z|,1e-15)·s)`, update axis
  label and marker texts; preserve the current X range and transform the Y range by
  `+log10(s_new/s_old)`.
* Zoom/pan: default ViewBox mouse interaction (left-drag pan, wheel zoom, right-drag axis zoom);
  `ViewBox.setMouseMode(pg.ViewBox.PanMode)`; context menu kept. After first data set, call
  `vb.setLimits(xMin=log10(f_start)-0.5, xMax=log10(f_stop)+0.5)`.
* **Default view / Reset view:** `_apply_default_view()` = `setLogMode(x=True, y=True)`,
  `vb.autoRange()` (bounds of the *visible* items; marker lines, texts and cross-hair are
  `ignoreBounds`; pyqtgraph's size-dependent default padding), then `vb.enableAutoRange()` so the
  view keeps fitting on curve-visibility and unit changes. `set_results` ends with it, so a fresh
  compute shows exactly the default view. `reset_view()` applies it, re-applies the marker
  visibility setting and emits `viewChanged`. Triggers: results-toolbar button "⟲ Reset view",
  View ▸ Reset View (`QAction`, shortcuts `Ctrl+D` and `Ctrl+0`,
  `Qt.ApplicationShortcut`; Edit ▸ Duplicate Row moved to `Ctrl+Shift+D`), a "Reset view" entry
  inserted at the top of the ViewBox context menu, and pyqtgraph's own "View All" entry, whose
  `triggered` signal is re-connected to `reset_view`. `is_default_view()` checks auto-range on and
  range == fitted range (tests). Unit switching: in the default view auto-range re-fits; after a
  manual zoom X is kept and Y shifted (above).
* Plot state: `ViewBox.sigRangeChanged` and auto-range toggles emit `ImpedancePlot.viewChanged`,
  which schedules an auto-save; `view_state() -> PlotView` and `apply_view_state(PlotView)` convert
  to/from the `session.plots` entry.
* Hover readout: `pg.SignalProxy(self.scene().sigMouseMoved, rateLimit=30, slot=self._on_move)`;
  map to view coordinates, nearest grid point in log f, show `f = 12.3 MHz, |Z| = 45.6 mΩ` in a
  `TextItem` in the top-left corner (pinned via `ViewBox` range change).
* Export PNG: `pyqtgraph.exporters.ImageExporter(self.plot).export(path)` with width 1600 px.
  (Import `pyqtgraph.exporters` explicitly so PyInstaller collects it.)
* Export image at a given size (`export_image(path, width, height, keep_zoom)`, used by All
  Plots): `make_export_copy()` builds an off-screen twin `ImpedancePlot` from the stored results and
  colours with the same unit, title, marker / plane-only / curve-visibility settings, removes the
  hover-readout row, sets `WA_DontShowOnScreen`, `resize(width, height)`, `show()`, and renders it
  twice (`processEvents()` + `grab()`) so axis text widths, legend and layout settle — a plot tab
  that was never shown otherwise exports with collapsed axes. Then default view (or the source
  plot's log10 ranges with `padding=0`), one more render, and export of the twin's **scene**
  (source rect = widget rect, hence exactly width × height): PNG via `ImageExporter` with
  width/height set using `blockSignal` (they are aspect-linked), SVG via `SVGExporter`. The twin is
  deleted afterwards; the on-screen plot is untouched.
* pyqtgraph ≤ 0.14 caveat: `SVGExporter.correctCoordinates` raises `ValueError` on the close-path
  token `Z` that Qt 6 writes for the ViewBox background path; `_svg_close_path_workaround()`
  temporarily wraps that module function to strip and re-append `Z` during the export.

### 5.7 Logging

`logging` to `%LOCALAPPDATA%/SimplePICalculator/logs/app.log` (rotating, 1 MB × 3); uncaught
exceptions via `sys.excepthook` → message box with traceback + log path.

### 5.8 Persistence: auto-save, projects, schema versioning

Goal: the user never loses work. The complete application state is written continuously to a
per-user auto-save file and restored at the next start; named project files are an additional,
explicit mechanism for sharing and versioning designs.

#### 5.8.1 Location

* At start-up, before creating any window: `QApplication.setApplicationName("SimplePICalculator")`,
  `QApplication.setOrganizationName("")` (empty, so no organisation folder is inserted),
  `QApplication.setApplicationDisplayName("Simple PI Calculator")`.
* Directory = `QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)`, created with
  `QDir().mkpath`. On Windows this is `%APPDATA%\SimplePICalculator` (e.g.
  `C:\Users\alice\AppData\Roaming\SimplePICalculator`); on Linux `~/.local/share/SimplePICalculator`.
* Override: environment variable `SPICAL_APPDATA_DIR` (used by tests and portable use).
* Files in that directory:

| File | Purpose |
|---|---|
| `autosave.spical.json` | current state (schema §4.7 incl. `session`) |
| `autosave.bak.spical.json` | previous successfully written state |
| `autosave.spical.json.tmp` | transient during a write |
| `autosave.lock` | `QLockFile` of the owning instance |
| `autosave.corrupt-YYYYMMDD-HHMMSS.spical.json` | quarantined unreadable files (keep newest 5) |
| `autosave.v<N>-newer-YYYYMMDD-HHMMSS.spical.json` | files written by a newer app version |

The Inno Setup uninstaller does **not** delete this folder (user data).

#### 5.8.2 Writing (debounced)

`gui/persistence.py`:

```python
class AutosaveManager(QObject):
    DEBOUNCE_MS = 1000
    status = Signal(str)                                   # short status-bar text
    def __init__(self, store: AutosaveStore, window: "MainWindow"): ...
    def acquire_lock(self) -> bool: ...                    # QLockFile.tryLock(200)
    @property
    def enabled(self) -> bool: ...                         # False if lock not acquired
    def schedule(self) -> None: ...                        # (re)start single-shot QTimer
    def flush(self) -> None: ...                           # stop timer, save now
    def collect_session(self) -> Session: ...
    def apply_session(self, session: Session) -> None: ...
```

* **Change sources** — every one calls `schedule()`: table models (`dataChanged`, `rowsInserted`,
  `rowsRemoved`, `modelReset`), form widgets (`valueChanged`, `textChanged`, `toggled`,
  `currentIndexChanged`), Excel import, splitter `splitterMoved`, tab `currentChanged`, decap filter,
  unit change, plane-only toggle, dock visibility, plot `viewChanged`, recent-files and project-path
  changes. Window move/resize is captured by overriding `moveEvent`/`resizeEvent` → `schedule()`.
* **Debounce:** `QTimer(singleShot=True, interval=1000)`; each `schedule()` restarts it, so a save
  happens 1 s after the last change.
* **Immediate flush** on: `closeEvent` (before accepting), `QApplication.aboutToQuit`, compute
  finished, before New/Open, after Save/Save As.
* **Procedure** (`AutosaveStore.save`):
  1. `doc = project_to_dict(project, anchor_dir=None, session)` (absolute paths); serialise with
     `json.dumps(doc, indent=2, ensure_ascii=False)`, but compare a canonical form **excluding**
     `session.saved_utc`; if identical to the last written content → skip (return False).
  2. Write UTF-8 bytes to `autosave.spical.json.tmp`, `flush()`, `os.fsync()`.
  3. If `autosave.spical.json` exists: `os.replace(autosave, autosave.bak)`.
  4. `os.replace(tmp, autosave)`.
  A crash between steps 3 and 4 leaves a valid backup, which §5.8.3 recovers.
* I/O errors (permission, disk full): logged; status bar message "Auto-save failed: …" at most
  once per 60 s; no modal dialogs; the next change retries.
* Results (impedance arrays) are **not** stored; they are recomputed (§5.8.3 step 6).
* If the lock is held by another running instance, this instance shows a yellow banner "Another
  Simple PI Calculator window owns the auto-save. Changes in this window are not auto-saved — use
  File ▸ Save As." and never writes the auto-save file (it starts with defaults and does not
  restore). Stale locks (crashed process) are handled by `QLockFile` (`setStaleLockTime(30000)` plus
  `removeStaleLockFile()` when `tryLock` fails and the owner PID is dead).

#### 5.8.3 Restoring at start-up, corruption handling

`AutosaveStore.load()` then `MainWindow` applies the result:

1. Acquire the lock (§5.8.2). Command-line `--no-restore` skips steps 2–5 (defaults are used; the
   next change overwrites the auto-save after rotating it to the backup). `--self-test` never
   touches the auto-save directory.
2. Read `autosave.spical.json`. It is **corrupt** if: the file cannot be decoded as UTF-8 JSON, the
   top level is not an object, `format` is wrong, `schema_version` is missing/not an int, or
   structural type checks fail (e.g. `pwr.rows` not a list, a row field of the wrong JSON type that
   cannot be defaulted). **Semantic problems are not corruption**: missing model/Excel files,
   invalid layer references, out-of-range values → load normally, report as ordinary validation
   issues in the Messages dock.
3. If the primary is missing → try the backup; if both missing → first run: defaults, no message.
4. If the primary is corrupt → quarantine it (`autosave.corrupt-<timestamp>.spical.json`) and try the
   backup. If the backup loads → use it, warning `W_AUTOSAVE_RECOVERED_BACKUP` ("The last auto-save
   was damaged; restored the previous auto-save from <time>"). If the backup is also corrupt → quarantine
   it too, start with defaults, warning `W_AUTOSAVE_CORRUPT` with the quarantine paths (Messages dock
   + status bar, non-modal). Keep only the newest 5 quarantined files.
5. If `schema_version` > `CURRENT_SCHEMA_VERSION` → rename to `autosave.v<N>-newer-<timestamp>…`,
   try the backup under the same rules, else defaults; warning `W_AUTOSAVE_NEWER` ("written by a newer
   version; kept as <path>").
6. Apply: inputs → models/widgets (signals blocked, no auto-save scheduled during apply); session →
   `restoreGeometry` (if the resulting frame does not intersect any `QScreen.availableGeometry`,
   centre on the primary screen), `restoreState`, splitter sizes, tabs, decap filter, dock visibility,
   recent files (non-existing entries kept but shown disabled), window title from `project_path` and
   `modified`. If `session.had_results` and `validate_inputs` reports no errors → start the compute
   automatically; when finished, apply `session.plots` view states for PWRs that still exist.

#### 5.8.4 Schema versioning and migration policy

* `schema_version` is a single integer shared by named projects and the auto-save;
  `CURRENT_SCHEMA_VERSION = 1` for v0.1.0 (the pre-release geometry with `height_mm`/`pad_*` and
  side options never shipped and has no migration). **Schema 2**: `vias.vias_per_decap` →
  `vias.vias_per_pad` (`MIGRATIONS[1] = migrate_1_to_2`, §2.6.4); frozen fixture
  `tests/data/project_v1.spical.json` (the v0.1 example project). **Schema 3**: `pwr.rows[].n_pads`
  (number of observation PADs; a change of meaning — schema 2 had exactly one PAD per net — hence a
  bump although the key has a default; `MIGRATIONS[2] = migrate_2_to_3` adds `n_pads: 1`); frozen
  fixture `tests/data/project_v2.spical.json` (the schema-2 example project). **Schema 4**:
  `decaps.distance_mode`, `decaps.sigma_mm`, `decaps.seed` (§2.5.5; `MIGRATIONS[3] = migrate_3_to_4`
  adds `"fixed"`, 0.5, 12345, so every schema-3 project computes identically); frozen fixture
  `tests/data/project_v3.spical.json` (the 0.2.0 example project).
* **No bump** for backward-compatible additive changes: a new optional key with a default. Old
  readers ignore unknown keys; new readers default missing keys.
* **Bump by 1** for any rename, removal, unit or meaning change. Every bump adds a pure function
  `MIGRATIONS[n] = migrate_n_to_n_plus_1(doc: dict) -> dict` (input doc of version n, output version
  n+1, never mutates its argument) and a frozen fixture `tests/data/project_v<n>.spical.json` that is
  kept forever and loaded by a test through the whole chain.
* `migrate()` applies `MIGRATIONS[v], MIGRATIONS[v+1], …` until `CURRENT_SCHEMA_VERSION`; a missing
  step → `ProjectFormatError`. Migration happens in memory before `project_from_dict` validation.
* Named project with older version: loaded, info `I_PROJECT_MIGRATED` ("converted from schema n"),
  marked modified. The file on disk is not touched until the user saves; on the first save of a
  migrated file, the original is first copied to `<name>.schema<n>.bak.spical.json` next to it.
* Named project with newer version → error `E_PROJECT_NEWER` (not loaded, current state unchanged).
  Auto-save with newer version → §5.8.3 step 5.
* `format` mismatch / invalid JSON in a named file → error `E_PROJECT_FORMAT` (current state
  unchanged).
* Writers always write `CURRENT_SCHEMA_VERSION` and the running `app_version`.

#### 5.8.5 File menu semantics

The auto-save always holds the state; named files are explicit snapshots.

* **New**: if `session.modified` and a named project is associated, or the state is untitled and
  contains any table rows → dialog "Save changes to <name|Untitled>?" [Save] [Discard] [Cancel]
  (Save → Save/Save As flow). Then reset all inputs to defaults, clear results and plot states, `project_path = None`,
  `modified = False`, flush auto-save.
* **Open…**: same prompt as New; `QFileDialog.getOpenFileName` with filter
  `"Simple PI Calculator project (*.spical.json);;JSON files (*.json);;All files (*)"`, start
  directory = folder of the current/last project. Load (§5.8.4 rules); on success replace state,
  `project_path = path`, `modified = False` (True if migrated), push to recent files, flush.
  On failure show a modal error and keep the current state.
* **Save** (Ctrl+S): if `project_path` is None → Save As; else `save_project` (atomic write, relative
  paths anchored at the project folder, no `session`), `modified = False`, push to recent, flush.
* **Save As…**: `QFileDialog.getSaveFileName`; if the chosen name does not end with `.spical.json`
  (case-insensitive), append `.spical.json` (replacing a bare `.json` suffix); relative paths are
  recomputed for the new folder; then as Save.
* **Recent Files ▸**: most recent first, max 8, de-duplicated by normalised absolute path
  (`os.path.normcase(os.path.abspath)`); selecting a missing file shows a message and removes it;
  "Clear Recent Files" empties the list.
* **Exit / window close**: flush the auto-save and quit **without** a save prompt; the state,
  including the modified marker and association with the named project, is restored next time.
* Window title: `Simple PI Calculator — <file name without .spical.json | Untitled>[*]`.

---

## 6. Help system

### 6.1 Rendering and constraints

Help is a set of static HTML 4-style pages shipped in `src/simple_pi_calculator/help/` and shown in
`HelpWindow(QMainWindow)` containing a left `QListWidget` table of contents and a `QTextBrowser`
(`setOpenExternalLinks(True)`, `setSearchPaths([help_dir])`). A toolbar has Back, Forward, Home,
"Open in Browser" (`QDesktopServices.openUrl(QUrl.fromLocalFile(current_page_path))`) and a find box
(`QTextBrowser.find`).

Help directory resolution: `importlib.resources.files("simple_pi_calculator") / "help"` converted
to a real path (in the PyInstaller onedir build the files exist on disk under
`_internal/simple_pi_calculator/help`, so the browser can open them).

QTextBrowser supports only Qt's "Supported HTML Subset" [QtHTML]. Pages MUST obey:

* Allowed tags: `html, head, title, body, h1–h6, p, br, hr, div, span, a (href, name), b, i, u, em,
  strong, code, tt, pre, sub, sup, ul, ol, li, dl, dt, dd, table, tr, th, td (colspan, rowspan,
  width), img (src, width, height, alt), blockquote, font (color)`.
* CSS: only in a `<style>` block in `<head>` or `style=` attributes, restricted to: `color`,
  `background-color`, `font-family`, `font-size` (px/pt), `font-weight`, `font-style`,
  `text-decoration`, `text-align`, `vertical-align` (sub/super/middle), `margin-*`, `padding` on
  table cells, `border-width`/`border-style`/`border-color` on tables, `white-space: pre`.
  Selectors: element, `.class`, `#id` only.
* **Not supported / forbidden:** JavaScript, flexbox, grid, float-based layout, `position`,
  `display` tricks, CSS variables, web fonts, `<svg>` inline, MathML, `<video>`, `<iframe>`, `<nav>`,
  `<section>` semantics (render as plain blocks at best), `:hover`, media queries.
* Tables: use attributes `border="1" cellspacing="0" cellpadding="4"` for visible borders.
* Images: PNG only, relative paths `img/xxx.png`, explicit `width` ≤ 760 px. SVG sources live in
  `docs/diagrams/` and are converted with `tools/render_diagrams.py` (QSvgRenderer → QImage, 2×
  resolution) — PNGs are committed so the build needs no SVG plugin.
* Equations: plain text in `<pre>` using Unicode (ω, ε, µ, √, Σ, ², ³) or images; no LaTeX.
* Encoding: `<meta charset="utf-8">`; file names ASCII lower-case with underscores.
* Internal links: relative `href="input_stackup.html#headers"`; external links (`https://…`) open
  in the system browser.
* Every page begins with the same header line of links (Home · Getting Started · Inputs · Physics ·
  References) implemented as a simple paragraph, not a nav bar.

### 6.2 Pages

| File | Content |
|---|---|
| `index.html` | What the tool does, PDN chain diagram, links to all pages |
| `getting_started.html` | Open example project, run, read markers; step-by-step with screenshots |
| `input_stackup.html` | Excel columns, accepted header variants, metal/dielectric detection, units, all E_/W_ codes, example table |
| `input_vias.html` | Drill/anti-pad diameter, via pitch (PWR–GND via spacing) and its effect on loop inductance, vias per decap pad, PAD vias (= vias per observation pad) and via-cluster ports, Top-side mounting, via length to the nearer plane, via model options |
| `input_pwr.html` | PWR list columns (incl. Number of PADs), GND reference choice, derived plane height and PAD row, validation messages |
| `input_decaps.html` | Decap list columns, Dummy Cap option (ports and caps per port), path resolution, axial placement and its approximations |
| `spice_models.html` | Supported .mod syntax, suffix table (incl. `1F` = 1 fF warning), PARAM scoping, examples |
| `touchstone.html` | s2p v1 format, series-through vs shunt-through formulas, extrapolation |
| `physics.html` | Cavity model, loss factor, port size, via model, Schur reduction, PAD side (several pads in parallel), DC behaviour, equations |
| `results.html` | Plot interaction, unit switch, markers, export formats |
| `project_file.html` | Saving: auto-save location and recovery, New/Open/Save/Save As/Recent, .spical.json keys and example, schema versions |
| `limitations.html` | §9 content in user language |
| `references.html` | §10 list |
| `about.html` | Version, MIT license text, third-party licenses (Qt/PySide6 LGPLv3, pyqtgraph MIT, numpy BSD, openpyxl MIT) |

### 6.3 Diagrams (SVG source → PNG)

| File | Shows |
|---|---|
| `pdn_chain.png` | Decap → via → plane cavity → via → PAD, with Z_decap, Z_via, Z_cav, Z_PAD labels |
| `stackup_cross_section.png` | Layer numbering from top, metal vs dielectric, PWR/GND pair, d, via lengths h_P, h_G, h_cav |
| `cavity_ports.png` | Rectangle W × H, coordinate origin, PAD port, decap ports, square port width w |
| `plane_placement.png` | Synthetic plane W × 1.4·D_ref: PAD row (N_pad pads, single PAD at W/2) at y = 0.2·D_ref, rows at y = 0.2·D_ref + d_k, x margins, 20 % end margins, a multi-row group |
| `dummy_cap.png` | Via set shared by a capacitor and its dummy neighbour; N = 5 → ports [2, 2, 1] |
| `via_loop.png` | PWR via + GND via current loop at pitch s_v, h_near to the nearer plane face, anti-pad segment t_near, cavity segment |
| `s2p_series_shunt.png` | Series-through vs shunt-through fixtures with formulas |
| `impedance_plot_annotated.png` | Screenshot of the plot with markers explained |
| `gui_overview.png` | Main window screenshot with numbered callouts |

---

## 7. Packaging & CI

### 7.1 PyInstaller (`packaging/simple_pi_calculator.spec`)

* Mode: **onedir**, windowed (`console=False`), `icon=resources/app.ico`, name
  `SimplePICalculator`, `version='version_info.txt'`.
* Entry script: `src/simple_pi_calculator/__main__.py` (which calls `app.main()`); `pathex=['src']`.
* `datas` (via `collect_data_files`):
  `collect_data_files('simple_pi_calculator', includes=['help/**/*', 'resources/**/*'])`, plus
  `('../examples', 'examples')`.
* `hiddenimports`:
  `collect_submodules('pyqtgraph')` (pyqtgraph imports Qt-binding-specific template modules
  dynamically, e.g. `pyqtgraph.graphicsItems.ViewBox.axisCtrlTemplate_pyside6`,
  `pyqtgraph.graphicsItems.PlotItem.plotConfigTemplate_pyside6`,
  `pyqtgraph.imageview.ImageViewTemplate_pyside6`), `pyqtgraph.exporters`, `PySide6.QtSvg`,
  `PySide6.QtPrintSupport` (used by pyqtgraph exporters), `openpyxl.cell._writer`.
* `excludes`: `tkinter`, `PySide6.QtWebEngineCore`, `PySide6.QtWebEngineWidgets`, `PySide6.Qt3DCore`,
  `PySide6.QtQuick`, `PySide6.QtQml`, `PySide6.QtMultimedia`, `PySide6.QtBluetooth`,
  `PyQt5`, `PyQt6`, `matplotlib`, `scipy` (core does not use it), `IPython`.
* Set environment `PYQTGRAPH_QT_LIB=PySide6` at the top of `app.py` **before** importing pyqtgraph
  (`os.environ.setdefault`) so pyqtgraph never probes PyQt.
* `upx=False` (antivirus false positives).

### 7.2 Inno Setup (`packaging/installer.iss`)

```
#define MyAppName "Simple PI Calculator"
#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif
[Setup]
AppId={{B1942B9F-0A78-4305-863E-D4ED531EF3E0}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=Simple PI Calculator contributors
DefaultDirName={autopf}\Simple PI Calculator
DefaultGroupName=Simple PI Calculator
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile=..\LICENSE
OutputDir=..\dist-installer
OutputBaseFilename=SimplePICalculator-{#MyAppVersion}-win64-setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\src\simple_pi_calculator\resources\app.ico
UninstallDisplayIcon={app}\SimplePICalculator.exe
[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; Flags: unchecked
[Files]
Source: "..\dist\SimplePICalculator\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{group}\Simple PI Calculator"; Filename: "{app}\SimplePICalculator.exe"
Name: "{group}\Examples"; Filename: "{app}\_internal\examples"
Name: "{group}\Uninstall Simple PI Calculator"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Simple PI Calculator"; Filename: "{app}\SimplePICalculator.exe"; Tasks: desktopicon
[Run]
Filename: "{app}\SimplePICalculator.exe"; Description: "Launch Simple PI Calculator"; Flags: nowait postinstall skipifsilent
```

(The AppId GUID above is fixed for the life of the product; never change it.) No file association in
v1 (the double extension `.spical.json` cannot be associated reliably). The uninstaller leaves the
per-user auto-save folder `%APPDATA%\SimplePICalculator` untouched.

### 7.3 Versioning

* Single source of truth: `src/simple_pi_calculator/__init__.py: __version__ = "X.Y.Z"` (SemVer).
* `pyproject.toml` uses `dynamic = ["version"]` with `[tool.setuptools.dynamic] version =
  {attr = "simple_pi_calculator.__version__"}`.
* Release = git tag `vX.Y.Z`; CI fails if the tag does not equal `__version__`.
* Non-tag builds use version `X.Y.Z-dev+<short sha>` for artifact names only (installer
  `MyAppVersion` = `X.Y.Z.<run_number>` style is not used; Inno gets `X.Y.Z`).
* `tools/write_version_info.py` generates the PyInstaller Windows `version_info.txt`
  (FileVersion `X.Y.Z.0`).

### 7.4 GitHub Actions (`.github/workflows/build-windows.yml`)

Triggers: `push` to `main`, `pull_request`, `push` tags `v*`, `workflow_dispatch`.

Job `test` (matrix: `windows-latest`, `ubuntu-latest`; Python 3.11):

1. `actions/checkout@v4`
2. `actions/setup-python@v5` with `python-version: "3.11"`, `cache: pip`
3. Ubuntu only: `sudo apt-get install -y libegl1 libxkbcommon-x11-0 libxcb-cursor0 libgl1`
4. `pip install -e .[dev]`
5. `pytest -q` with env `QT_QPA_PLATFORM=offscreen`

Job `build` (`windows-latest`, needs `test`):

1. checkout, setup-python 3.11
2. `pip install .[dev]`
3. Check tag vs version (tag builds): PowerShell compares `${{ github.ref_name }}` to
   `v$(python -c "import simple_pi_calculator as m; print(m.__version__)")`.
4. `python tools/write_version_info.py packaging/version_info.txt`
5. `pyinstaller packaging/simple_pi_calculator.spec --noconfirm --clean` (working-directory
   `packaging`, `distpath ../dist`, `workpath ../build`)
6. Smoke test: `dist/SimplePICalculator/SimplePICalculator.exe --self-test` (the app supports a
   `--self-test` CLI flag: imports all modules, runs the example project headless via `engine`,
   prints `SELF-TEST OK` and exits 0 without creating windows; timeout 120 s).
7. Inno Setup: `if (-not (Get-Command iscc -ErrorAction SilentlyContinue)) { choco install innosetup
   --no-progress -y }`; then `iscc /DMyAppVersion=$version packaging\installer.iss` (the default
   install path `C:\Program Files (x86)\Inno Setup 6\ISCC.exe` is added to PATH if needed).
8. `actions/upload-artifact@v4`: `dist-installer/*.exe` and a zip of `dist/SimplePICalculator`.
9. Tag builds only: `softprops/action-gh-release@v2` attaching the installer and zip, release notes
   from `CHANGELOG.md` section if present.

---

## 8. Test plan

Framework: pytest (+ pytest-qt for GUI smoke tests). All numerical tests are headless. Fixtures that
need .xlsx files generate them in `tmp_path` with openpyxl inside the test (no binary fixtures
except the bundled examples). Relative tolerance notation: `rel=`.

### 8.1 `test_units_constants.py`

* `parse_spice_number` table (§4.5 examples) exact to rel 1e-12.
* mm→m conversions; display scale factors.

### 8.2 `test_stackup.py`

* Bundled example: 11 layers, metal = {1,3,5,7,9,11}, T_total = 1.41 mm, z_c(5) = 0.2875 mm,
  z_c(3) = 0.1525 mm.
* `derive_plane_pair(5,3)`: d = 0.1 mm, εr_eff = 4.3, tanδ_eff = 0.018.
* Two-prepreg case (§2.2, exact complex form): εr_eff = 3.733426, tanδ_eff = 0.01466592 (rel 1e-6);
  extreme mix (3.0/0.002 + 4.5/0.03, 0.05 mm each): 3.600677, 0.01319398; equal-εr case equals the
  thickness-weighted average (rel 1e-12).
* Metal between PWR and GND → `W_STACK_METAL_BETWEEN`, d includes metal thickness.
* Errors: dielectric PWR layer (`E_PWR_LAYER_NOT_METAL`), same layer, missing layer, no dielectric.
* `C_plane(20 mm, 20 mm, d=0.1 mm, εr=4)` = 141.667 pF (rel 1e-5); 60×21 mm, εr 4.3 → 479.720 pF;
  30×14 mm, εr 4.3 → 159.907 pF.

### 8.3 `test_cavity.py`

1. **DC capacitive limit:** a = b = 20 mm, d = 0.1 mm, εr = 4, tanδ = 0, σ = inf, port at centre,
   D = 0.2 mm, f ∈ {1 kHz, 10 kHz}: `Z_00 · jωC_plane` = 1 within rel 1e-4 (prototype and review:
   1.0e-6, set by the tanδ floor of §3.4). With tanδ = 0.02: Z_00 → 1/(jωC(1 − 0.02j)) within rel 1e-3 at 1 kHz.
2. **DC limit with finite conductors:** σ = 5.8e7, t = 35 µm: |Z_00 · jωC_plane − 1| < 1e-3 at 1 kHz
   (prefactor Γ_c rule).
3. **Resonance:** a = 100 mm, b = 80 mm, d = 0.1 mm, εr = 4, tanδ = 0.001, σ = inf, port at (2, 2) mm,
   linear sweep 600–1000 MHz step 0.5 MHz: local maxima of |Z_00| at 749.5 ± 1.0 MHz and
   937.0 ± 1.0 MHz; analytic `resonance_frequency(1,0)` = 749.481 MHz, `(0,1)` = 936.851 MHz.
4. **Port at nodal line:** port at (a/2, 2 mm) → the (1,0) peak is absent (|Z| at 749.5 MHz lower
   than at 700 MHz neighbours' trend), (0,1) peak present.
5. **Loop inductance (image theory):** a = b = 100 mm, d = 0.1 mm, εr = 4, σ = inf, tanδ = 0, D = 0.5 mm,
   ports at (50,50) and (60,50) mm, f = 1 MHz:
   L = Im(Z_00 + Z_11 − 2Z_01)/ω = (μ0 d/π) ln(10/0.25) = 0.14756 nH within rel 2 %
   (prototype 0.1482 nH).
6. **Internal inductance of thin planes:** same as 5 but σ = 5.8e7, t = 35 µm → L ≈
   (μ0 (d + 2t/3)/π) ln(s/r0) = 0.1820 nH within rel 2 % (prototype 0.1826 nH).
7. **Symmetry & reciprocity:** Z_ij = Z_ji (abs tol 1e-12·|Z|).
8. **Static split accuracy:** compare `z_matrix` with a brute-force full dynamic sum
   (no split, same M, N) on a 20×20 mm, 3-port case at 1 MHz and 1 GHz: rel 1e-3.
9. **Mode count rules:** (K_split from the lossy k_max) 20×20 mm, D = 0.2 mm, f_stop = 1 GHz, εr = 4 → w = 0.22369 mm,
   M = N = ceil(3·20/0.22369) = 269; 100×100 mm, D = 0.2 → M = 1342; 400×400 mm, D = 0.1 →
   capped at 1500 with `capped=True`.
10. `surface_impedance`: thick limit (t = 1 mm, 1 GHz) equals (1+j)/(σδ) rel 1e-6; thin limit
    (t = 35 µm, 1 kHz) Re → 1/(σt) = 4.926e-4 Ω rel 1e-3.
11. **Via-cluster port (§2.4.5):** `cluster_port_width` for D = 0.2 mm, s_v = 1 mm: n = 1, 2, 4, 9 →
    0.223689, 0.841204, 1.778930, 3.451317 mm (rel 1e-5). Loop test on 30 × 14 mm, d = 0.1 mm, εr 4,
    σ = ∞, tanδ 0, 1 MHz, decap single-via port at (15, 12) mm shorted: 4 explicit ports at
    (15 ± 0.70711, 2 ± 0.70711) mm in parallel → 0.16653 nH; one cluster port (w = 1.77893 mm) at
    (15, 2) → 0.16760 nH; the two agree within 2 %.
12. **Per-port widths:** a matrix built with widths [w_a, w_b] is symmetric and its self terms match
    single-width models built with w_a and w_b respectively (rel 1e-12).
13. **Singular check (F12):** two coincident ports with zero load and `W_PORT_OVERLAP` → `E_SINGULAR`
    raised (no NaN result returned).

### 8.4 `test_placement.py`

All with D_drill = 0.2 mm ⇒ w = 0.223690 mm; coordinates compared with abs 1e-9 m unless noted.

1. `plane_height(60 mm, [8, 15] mm)` → (H = 21 mm, D_ref = 15 mm). `plane_height(30 mm, [])` →
   (30 mm, 21.428571 mm); PAD at (15, 4.285714) mm.
2. `ports_for_row` / `caps_per_port_for_row`: (1, false) → 1, [1]; (1, true) → 1, [1] + `I_DUMMY_SINGLE`;
   (2, true) → 1, [2]; (4, true) → 2, [2, 2]; (5, true) → 3, [2, 2, 1]; (5, false) → 5, [1]*5;
   (7, true) → 4, [2, 2, 2, 1]. Invariant Σ caps = N for N = 1…50, both flags.
3. **VDD_CORE example:** PAD (30, 3) mm; m_x = 6.111845 mm, L_x = 47.776310 mm; row 0 (10 ports):
   y = 11 mm, x_0 = 8.500660 mm, pitch 4.777631 mm; row 1 (4 ports): y = 18 mm,
   x = 12.083884, 24.027961, 35.972039, 47.916116 mm; `group_index` = [0]*10 + [1]*4;
   `caps_per_port` all 1; height 21 mm.
4. **VDD_IO example (dummy):** W = 30 mm, rows (4, 5 mm, dummy) and (1, 10 mm) → H = 14 mm, PAD
   (15, 2) mm, ports (9.055922, 7), (20.944078, 7), (15, 12) mm; `caps_per_port` = [2, 2, 1].
5. **Multi-row:** W = 10 mm, one row N = 100, d = 5 mm → D_ref = 5, H = 7 mm, m_x = 1.111845 mm,
   L_x = 7.776310 mm, L_x/100 < w → n_row = 34, R = 3; sub-rows at y = 5.776310, 6.000000,
   6.223690 mm with 34, 34, 32 ports; first x of sub-rows 0 and 2 = 1.226203 mm and 1.233355 mm;
   no two ports overlap; ports stay inside [w/2, W − w/2].
6. `E_PWR_WIDTH_TOO_SMALL` for W = 0.5 mm; no error for W = 0.6 mm (L_x = 0.2563 mm ≥ w).
7. `E_DREF_TOO_SMALL` for D_ref = 0.5 mm (0.1 mm < w/2); `E_DECAP_DISTANCE` for d = 0.
8. `W_DECAP_TOO_CLOSE` for d = 0.2 mm; `W_PORT_OVERLAP` for two rows N = 10 at d = 5.0 and 5.1 mm
   in W = 60 mm (same x cells, Δy = 0.1 mm < w).
9. `W_DECAP_CLIPPED`: W = 5 mm, N = 1000, d = 5 mm (63 sub-rows spanning 13.9 mm > H = 7 mm);
   all y within [w/2, H − w/2].
10. Disabled decap rows are ignored for D_ref.
11. **PAD row (§2.5.1):** `n_pads = 1` gives bit-identical coordinates and widths to the call without
    `n_pads` (PAD at (15, 2) mm for VDD_IO). VDD_IO with N_pad = 4, w_pad = 0.5 mm: pads at
    y = 2 mm, x = m_p + (i + 0.5)·L_p/4 (m_p = 3.25 mm, L_p = 23.5 mm), port widths [w_pad]×4 +
    [w]×3, `group_index` = [0, 0, 1], decap ports unchanged at (9.055922, 7), (20.944078, 7),
    (15, 12) mm. Crowded: W = 5 mm, d = 5 mm, N_pad = 30 → 16 + 14 pads on sub-rows y = 1 mm ∓ w/2.
    `E_PWR_NPADS` for N_pad = 0; `E_PWR_WIDTH_TOO_SMALL` for W = 1 mm, w_pad = 0.5 mm, N_pad = 2 (not
    for N_pad = 1); `W_PAD_CLIPPED` for W = 5 mm, d = 1 mm, N_pad = 100 with all pad y within
    [w_pad/2, H − w_pad/2].
12. **Distance distribution (§2.5.5, `test_distance_distribution.py`):** Φ/Φ⁻¹ round trip < 1e-13;
    truncated samples in [−1, 1] with mean 0 and variance 0.29113 (200 000 samples, fixed seed); one
    stream (rows are consecutive slices of one draw), same seed identical, different seed different;
    P_k samples per row (ceil(N/2) for Dummy Cap); per-port y = fixed position + (d_kj − d_k) incl.
    sub-rows, x unchanged, D_ref = max d_k + σ, H = 1.4·D_ref identical across seeds; `W_DECAP_CLIPPED` for a sampled d < 0;
    engine: fixed mode identical to the default path, σ = 1e-7 mm within 1e-6 of fixed, all samples in
    [d − σ, d + σ], sample mean of 2000 via sets within 25 µm of d, table-order stream shared by all
    nets (single-net run identical), cache key contains mode/σ/seed (warm = cold), export header line;
    GUI (`test_gui_distance.py`): toggling mode/σ/seed marks the project modified, enables the
    controls, scatters the preview (tooltip), changes the results, persists in the project file and the
    auto-save; Fixed again reproduces the fixed result bit-identically.

### 8.5 `test_via.py`

D_drill = 0.2 mm, D_antipad = 0.5 mm, Cu, t_pl = 25 µm unless noted; rel 1e-5 for inductances.

* `partial_mutual_inductance(1 mm, 1 mm)` = 0.093432 nH; `(1 mm, 0.1 mm)` = 0.418647 nH.
* `pair_inductance`: (1 mm, 1 mm) = 0.765061 nH; (0.1525 mm, 0.5 mm) = 0.049604 nH;
  (1.1225 mm, 1.0 mm) = 0.875391 nH; (5 mm, 1.0 mm) = 4.430114 nH; ratio to (μ0h/π)ln(s/r0) → 1
  within 3 % for h = 100 mm, s = 1 mm.
* `antipad_inductance(35 µm)` = 0.0064140 nH; legacy `coax_inductance(1 mm)` = 0.183258 nH;
  `goldfarb_pucel_inductance(1 mm)` = 0.328148 nH.
* `via_geometry` for the example stack-up: PWR 5/GND 3 → nearer 3, h_near 0.135 mm, t_near 0.035 mm,
  h_R 0.305 mm; PWR 7/GND 9 → nearer 7, h_near 1.105 mm, h_R 2.245 mm; PWR 3/GND 1 → nearer 1,
  h_near 0, h_R = 0.035 mm, `W_VIA_ZERO_LENGTH`, L_loop = L_ap = 0.0064140 nH.
* `loop_inductance` (s_v = 1 mm): model `pair` → 0.054411 nH (PWR 5/3), 0.866012 nH (PWR 7/9);
  `goldfarb_pucel` → 0.021836 nH and 0.544438 nH; `coax` → 0.055894 nH and 0.411415 nH.
* `via_resistance`: 1.2544 mΩ @1 kHz, 1.3369 mΩ @1 MHz, 4.8665 mΩ @100 MHz, 13.826 mΩ @1 GHz
  (1 mm) rel 1e-3; at 1 MHz 0.40775 mΩ (0.305 mm) and 3.00130 mΩ (2.245 mm).
* vias_per_pad = 2 halves and 4 quarters the above-plane via-set impedance (odd counts valid);
  vias_per_pad = 0 or 1.5 → `E_VIA_COUNT`;
  antipad ≤ drill → `E_VIA_ANTIPAD`; via pitch 0.2 mm (= drill) → `E_VIA_PITCH`; 0.3 mm →
  `W_VIA_PITCH_SMALL`.
* `port_loads`: synthetic Z_decap = 1 Ω, Z_via,dec = 0.1 Ω, L_mount = 0, caps [2, 2, 1] →
  [0.6, 0.6, 1.1] Ω; with L_mount = 1 nH at 1 MHz, a 2-cap port = (1 + j·6.2832e-3)/2 + 0.1 Ω.

### 8.6 `test_spice_parser.py` (text fixtures inline)

1. Series RLC flat subckt → 3 elements, values exact.
2. Suffixes & trailing units: `10uF`, `0.45nH`, `30mOhm`, `1MEG`, `1Meg`, `3mil`, `1F` (=1e-15).
3. Continuation `+` lines, `*` full-line, `;` and `$` inline comments, tabs, `C = 1n` spacing,
   parentheses in node lists `X1 (a b) SUB`.
4. `.PARAM` with arithmetic: `.PARAM A=2 B={A*3+1} C='sqrt(B)*1n'` → B = 7, C = 2.6458e-9.
5. Nested X with `PARAMS:` defaults and overrides (bundled cap_0603_10uF.mod): flattened names
   `x_esl.l1`, `x_esl.l2`, `x_esl.r1`, `c1`, `r2`, `rleak`; L1 = L2 = 0.25 nH, RS = 3 mΩ.
6. K parsing: `K1 L1 L2 0.5`; multi-inductor `K1 L1 L2 L3 0.2` → 3 pair couplings.
7. Errors with line numbers: unknown subckt, `V1` element (`E_SPICE_UNSUPPORTED`), `.INCLUDE`,
   missing `.ENDS`, 3-pin top subckt, recursion `A` instantiating `A`, unknown param, division by
   zero, `|k| > 1`.
8. Top selection: file with two unreferenced subckts → first 2-pin one + `W_SPICE_MULTI_TOP`;
   explicit `subckt` argument overrides.
9. Global node `0` inside subckt → merged with pin2 + warning.
10. Expression evaluator rejects Python injection (`__import__('os')`) with `E_SPICE_EXPR`.

### 8.7 `test_mna.py`

* Series RLC (R = 30 mΩ, L = 0.45 nH, C = 100 nF) vs analytic over 1 kHz–1 GHz: rel 1e-9
  (gmin effect < 1e-9 relative, since |Z| ≪ 1/gmin).
* Parallel RLC resonance: R = 1 Ω ∥ L = 1 µH ∥ C = 1 nF → |Z(f0 = 5.0329 MHz)| = 1 Ω (rel 1e-6).
* L = 0 and R = 0 branches: `R1 1 2 0` → Z = 0 (abs < 1e-12); `L1 1 2 0` idem.
* C = 0 element ignored; floating node through C only still solvable.
* Coupled inductors: L1 = L2 = 1 nH in series aiding, k = 0.5 → L_eq = 3 nH; opposing (swap nodes of
  L2) → 1 nH; rel 1e-9 at 1 MHz.
* Bundled 10 µF model: SRF of |Z| minimum at 2.2508 MHz (±0.5 % on a fine grid), |Z_min| = 5.0 mΩ
  (rel 1 %, RLEAK negligible).
* Batch vs per-frequency loop equality.

### 8.8 `test_touchstone.py`

* Generate synthetic S21 from a series RLC with Z0 = 50: series-through S21 = 2Z0/(2Z0+Z); reading
  back with mode `series` recovers Z (rel 1e-9) in RI, MA, DB formats and HZ/KHZ/MHZ/GHZ units.
* Shunt-through S21 = 2Z/(2Z+Z0) → mode `shunt` recovers Z (rel 1e-9).
* Wrong mode gives a clearly different Z (sanity, > 10× at 1 MHz).
* v1 data order 11,21,12,22 (asymmetric S12 ≠ S21 fixture detects swapped order).
* Multi-line records, comments, missing option line (defaults GHZ S MA R 50), `[Version] 2.0` →
  `E_S2P_V2`, Y-parameters → `E_S2P_PARAM`, noise block ignored.
* Interpolation: log-log interpolation of an ideal capacitor at mid points rel 1e-3; low
  extrapolation of a capacitor exact (rel 1e-9); high extrapolation of inductor exact.

### 8.9 `test_excel_headers.py`

* Normalisation examples: `"Thickness (mm)"` → (`thickness`, `mm`); `"tan δ"` → (`tand`, None);
  `"Conductivity(S/m)"` → (`conductivity`, `s/m`); `"Layer No."` → (`layerno`, None).
* Header variants from §4.2 each map to the correct field; header row found in row 3 when rows 1–2
  contain a title; missing Df column → `E_XL_HEADER_NOT_FOUND` naming `Df`.
* "Layer Name" not captured as layer_number when both exist in either column order.
* PWR list: `GND Layer Number` → gnd_layer and `Layer Number` → pwr_layer regardless of order.
* Decap list: `Decap File Name` → model_file, `Number of Decaps` → count, `Distance to PAD (mm)` →
  distance, `Dummy Cap` → dummy; values `Yes`, `no`, `TRUE`, `False`, `1`, `0`, Excel booleans, empty
  map correctly; `maybe` → `E_XL_BOOL`; column absent → all false.
* PWR list with a legacy `PWR Plane Height` column → `W_XL_COLUMN_IGNORED`, no error.
* PWR list `Number of PADs`, `PAD Count`, `# PADs`, `PADs`, `pads`, `No. of Pads`, `Pad Qty` → n_pads
  (values 4 and empty → 4, 1; not ignored); column absent → 1 and `PAD X (mm)` still ignored with
  `W_XL_COLUMN_IGNORED`; values 0 and 2.5 → two `E_PWR_NPADS`.
* Unit handling: `Thickness(um)` with value 35 → 0.035 mm; `Thickness(mil)` 1.378 → 0.0350 mm.
* Numbers as text `"5.8E7"`; bad text → `E_XL_NUMBER` with cell reference; formula without cached
  value → `E_XL_FORMULA_NO_VALUE`.

### 8.10 `test_project_io.py` and `test_autosave_store.py`

Project files:

* Round-trip save/load equality (incl. `dummy`); relative path conversion; named file contains no
  `session`; a `session` block in a named file is ignored with info; unknown key warning; defaults
  applied when optional keys (e.g. `dummy`) missing; atomic write leaves no `.tmp` files.
* `E_PROJECT_NEWER` for `schema_version` = current + 1; `E_PROJECT_FORMAT` for invalid JSON / wrong `format`.
* `migrate_1_to_2`: `vias_per_decap` 2/4/6/8/3/1/0 → `vias_per_pad` 1/2/3/4/2/1/1, input not mutated;
  the frozen `tests/data/project_v1.spical.json` loads through the chain (`I_PROJECT_MIGRATED`,
  no unknown-key warning) with the same inputs as the current example project and `n_pads` = 1.
* `migrate_2_to_3`: adds `n_pads: 1` to rows without it, keeps an existing value, does not mutate
  its input; the frozen `tests/data/project_v2.spical.json` loads through the chain
  (`I_PROJECT_MIGRATED`, `migrated_from` = 2, no unknown-key warning) with PWR rows equal to the
  current example project and saves as schema 3 with `n_pads` = 1. Round trip keeps `n_pads` = 3.
* Migration framework (monkeypatch `CURRENT_SCHEMA_VERSION = 2`, `MIGRATIONS = {1: fn}` renaming a
  key): v1 file loads migrated, `I_PROJECT_MIGRATED`, modified; first save creates
  `<name>.schema1.bak.spical.json`; migration function does not mutate its input; missing step →
  `ProjectFormatError`.
* Save As suffix rule: `board` → `board.spical.json`; `board.json` → `board.spical.json`;
  `Board.SPICAL.JSON` unchanged.

`AutosaveStore` (tmp directory, no Qt):

1. First run (empty dir) → source `defaults`, no issues.
2. Save then load → identical project and session (source `primary`); second save with identical
   content returns False and does not change the file mtime; after two differing saves the backup
   holds the previous content.
3. Primary truncated to half its bytes, valid backup → source `backup`,
   `W_AUTOSAVE_RECOVERED_BACKUP`, a `autosave.corrupt-*.spical.json` file exists.
4. Both corrupt → source `defaults`, `W_AUTOSAVE_CORRUPT`, two quarantined files; creating 7 corrupt
   events keeps only the newest 5 quarantine files.
5. Primary with `schema_version` 99 → renamed `autosave.v99-newer-*`, backup used or defaults,
   `W_AUTOSAVE_NEWER`.
6. Primary referencing a non-existent model file → loads normally (source `primary`), no corruption
   warning.
7. Simulated crash: backup present, primary missing → source `backup`.
8. `SPICAL_APPDATA_DIR` override honoured by the GUI-side path resolver.

### 8.11 `test_pdn.py` (reduction and end-to-end)

1. `reduce_ports` with a hand-made 2-port: z = [[z00, z01],[z01, z11]], load Z_L → z00 − z01²/(z11+Z_L)
   (rel 1e-12); Z_L → ∞ (1e12 Ω) returns z00 (rel 1e-9); K = 0 returns z00.
2. **Single decap, low frequency:** W = 20 mm, one ideal 100 nF capacitor (C only) at d = 5 mm
   (H = 7 mm, C_plane = 49.58 pF for d = 0.1 mm, εr = 4), 10 kHz: |Z_PAD| ≈ 1/(ω(C + C_plane)) within
   rel 1e-3.
3. **Dummy equivalence:** a dummy row of N = 2 on one via set equals a non-dummy row N = 1 with the
   model impedance halved (rel 1e-9); the dummy flag with N = 1 equals the non-dummy result exactly.
4. **End-to-end bundled example** using `examples/example_project.spical.json` (default sweep
   100 kHz–1 GHz / 400 points; via model `pair`, D_drill 0.2 mm, D_antipad 0.5 mm, via pitch
   1.0 mm, 1 via per decap pad (one PWR + one GND via per decap, = schema-1 `vias_per_decap = 2`), 1 PAD via pair, L_mount = 0, plating 25 µm, via σ 5.8e7 S/m; plane metal
   Cu 35 µm; decap models of §4.5). Reference values from the design-time prototype implementing
   exactly §2–§3 (v1.2) with the geometry of §2.5; K_split from k_max evaluated on the 400-point grid.
   Tolerance rel 5 % unless noted (to allow legitimate implementation differences such as gmin).
   Marker readouts are the exact values.

   | Quantity | VDD_CORE | VDD_IO |
   |---|---|---|
   | PWR / GND layer, d, εr, tanδ | 5 / 3, 0.1 mm, 4.3, 0.018 | 7 / 9, 0.1 mm, 4.3, 0.018 |
   | W × H (D_ref) | 60 × 21 mm (15 mm) | 30 × 14 mm (10 mm) |
   | PAD | (30, 3) mm | (15, 2) mm |
   | Decap ports (caps) | 10 × 0402 at y = 11 mm, 4 × 10 µF at y = 18 mm (§8.4 #3) | 2 ports × 2 caps 0402 at y = 7 mm, 1 × 10 µF at (15, 12) mm |
   | h_near, h_R | 0.135 mm, 0.305 mm | 1.105 mm, 2.245 mm |
   | L_loop per via pair (L_pair + L_ap) | 0.054411 nH | 0.866012 nH |
   | Port widths (PAD, decap) | 0.22369 mm, 0.22369 mm | 0.22369 mm, 0.22369 mm |
   | C_plane | 479.72 pF | 159.91 pF |
   | Modes M × N (dynamic) | 805 × 282 (6) | 403 × 188 (2) |
   | \|Z\| @ 100 kHz | 38.69 mΩ (rel 3 %; analytic 38.82 mΩ for 41 µF) | 152.0 mΩ (rel 3 %; analytic 153.0 mΩ for 10.4 µF) |
   | \|Z\| @ 1 MHz | 3.294 mΩ | 12.37 mΩ |
   | \|Z\| @ 10 MHz | 35.42 mΩ | 62.72 mΩ |
   | \|Z\| @ 100 MHz | 139.5 mΩ | 881.7 mΩ |
   | \|Z\| @ 1 GHz | 2.902 Ω (rel 10 %, near f_10 = 1.205 GHz) | 4.494 Ω (rel 10 %) |
   | Plane-only \|Z\| @ 1 MHz | 331.7 Ω (rel 3 %) | 995.1 Ω (rel 3 %) |

   Variant **VDD_IO with PAD via count = 4** (all else identical; PAD cluster port w_pad = 1.77893 mm
   at (15, 2) mm; Z_via,pad = Z_viapair/4): |Z| = 152.3 mΩ @100 kHz, 10.88 mΩ @1 MHz, 20.79 mΩ @10 MHz,
   445.7 mΩ @100 MHz, 162.5 mΩ @1 GHz (rel 10 %); plane-only @1 MHz 995.1 Ω.

   On first passing implementation, store full-curve golden data in
   `tests/data/golden_example.json`; subsequent regressions compare rel 1e-6.

   **Several PADs (§2.5.1, §2.8)** (`test_pdn.py`, example project, all else as above):

   * (i) N_pad = 1 is the old code path: explicit `n_pads = 1` equals the example result (Z_PAD,
     markers, plane-only) to rel 1e-12; `reduce_ports(n_pads=1)` equals an independent v0.1
     single-PAD reduction to rel 1e-12 and `combine_pads` on the 1×1 matrix equals Z_red + Z_via,pad
     to rel 1e-12. The golden data and the table above are unchanged.
   * Algebra: N_pad coincident pads (identical rows/columns) with Z_via each = one pad with
     Z_via/N_pad (rel 1e-9). Four single-via pads at the §2.4.5 cluster positions (30 × 14 mm, σ = ∞,
     shorted decap at (15, 12) mm, 1 MHz) → 0.16653 nH (rel 5e-3), one cluster port within 2 %.
     Coincident pads with zero via impedance → `SingularReductionError`. Worker count and exact vs
     probe rcond do not change Z_pp,red or Z_PAD (rel 1e-12); Z_PAD equals 1/Σ(A⁻¹) (rel 1e-10).
   * (ii) N_pad = 4 (1 PAD via pair per pad; regression of this implementation, rel 1e-3):

     | \|Z\| | 100 kHz | 1 MHz | 10 MHz | 100 MHz | 1 GHz |
     |---|---|---|---|---|---|
     | VDD_IO, 4 PADs (P = 7) | 152.28 mΩ | 10.786 mΩ | 18.742 mΩ | 425.88 mΩ | 110.05 mΩ |
     | VDD_CORE, 4 PADs (P = 18) | 38.724 mΩ | 3.1319 mΩ | 28.767 mΩ | 57.218 mΩ | 2.2357 Ω |

     These pads are spread across the width (x = 6.084, 12.028, 17.972, 23.916 mm at y = 2 mm for
     VDD_IO), so the case is **not** geometrically equivalent to the "VDD_IO with PAD via count = 4"
     variant (one 1.77893 mm cluster port at (15, 2) mm: 152.3 / 10.88 / 20.79 / 445.7 / 162.5 mΩ):
     the test checks that variant separately and that 4 spread pads give a lower |Z| @100 MHz
     (425.9 < 445.7 mΩ); 1 GHz lies near the plane resonances and differs most. Plane-only @1 MHz
     995.1 Ω (rel 3 %).
   * Monotonicity: |Z| @100 MHz decreases for N_pad = 1 → 2 → 4 (VDD_IO 881.7 → 574.5 → 425.9 mΩ,
     VDD_CORE 139.5 → 82.14 → 57.22 mΩ). (At 100 kHz |Z| rises by < 0.2 % because the lower series
     inductance cancels less of the capacitive reactance.)
   * `E_PWR_NPADS` from `validate_inputs` and `compute_project` for N_pad = 0; the cavity cache key
     differs for N_pad = 1 and 2 with identical ports.
5. A PWR with a missing model file fails alone while the other PWR still returns a result (engine
   isolates per-PWR errors). A PWR without decap rows returns the plane-only result for a W × W
   plane with `W_PWR_NO_DECAPS`.
6. Cancellation: cancel callback returning True after the first poll raises `CancelledError`.
7. Performance guard (marked `slow`): W = 100 mm, one row of 50 decaps at d = 70 mm (H = 98 mm,
   P = 51), D = 0.2 mm, 400 points completes in < 60 s on CI.

### 8.12 GUI smoke tests (`test_gui_smoke.py`, pytest-qt, offscreen)

* MainWindow opens (with an empty `SPICAL_APPDATA_DIR`), loads example project, runs compute via
  worker, `finished` signal within 60 s, plot tabs == 2, readout table has 3 numeric columns.
* Unit switch changes axis label text to `|Z| (µΩ)` and scales curve data by 1e3 relative to mΩ.
* InfiniteLine positions equal log10(marker frequency).
* Help window loads `index.html` without missing images (`QTextDocument` resource check for each
  `<img>`).
* `--self-test` CLI returns exit code 0 and creates no files in `SPICAL_APPDATA_DIR`.
* Auto-save (with `SPICAL_APPDATA_DIR=tmp_path`): change the drill spin box → within 1.5 s
  (`qtbot.waitUntil`) `autosave.spical.json` contains the new `drill_diameter_mm`; three changes
  within 0.5 s produce exactly one write (patch `AutosaveStore.save` to count).
* Restore: close the window (flush), create a new `MainWindow` → table rows, drill value, Dummy Cap
  checkboxes, splitter sizes, input tab, decap filter, recent files, window title marker restored;
  with `had_results` the compute starts automatically and the stored plot X range is re-applied.
* Corrupt auto-save on start → window opens with defaults and a `W_AUTOSAVE_CORRUPT` entry in the
  Messages dock (no modal dialog).
* Second `MainWindow` while the first holds the lock → auto-save disabled banner visible; its edits
  do not modify the file.
* PWR table derived Height column updates from 21.0 to 28.0 mm when the VDD_CORE 15 mm row is edited
  to 20 mm; `PlacementPreview` paints without exceptions.
* Save As with name `board` writes `board.spical.json`; Recent Files lists it first.
* Dummy Cap / count edits reach the computation: example → VDD_CORE counts ×2 via `setData`, Dummy
  Cap via `CheckStateRole` → Run → markers 2.306 / 18.13 / 129.0 mΩ (differ > 5 % from 3.294 / 35.42 /
  139.5 mΩ); a count typed into an open cell editor and a via count typed into a spin box are
  committed by Run; a click in the middle of a check-box cell toggles, a double click toggles once.
* "Vias per decap pad" spin box (min 1, odd values kept) and "PAD vias (per observation pad)" labels.
* PWR table `# PADs` column: header text and tooltip; editing VDD_CORE to 4 stores `n_pads = 4`, the
  preview placement has N_pad = 4 and 18 ports and paints; `make_inputs` carries `n_pads`; a value
  of 0 shows `E_PWR_NPADS` in the cell tooltip.

### 8.13 Help lint (`test_help_html.py`)

* Parse each help page with `html.parser`; fail on forbidden tags (`script`, `svg`, `iframe`,
  `video`, `math`, `canvas`), on CSS properties `display`, `flex`, `grid`, `position`, `float`;
  all relative links and images resolve to existing files.

---

## 9. Limitations (v1)

1. **No VRM / DC path.** Z_PAD is capacitive (→ ∞) at low frequency; realistic PDNs flatten to the
   VRM output impedance below ~10–100 kHz. Bulk capacitors can be entered as decap rows.
2. **Rectangular, solid planes only**; no cut-outs, split planes, irregular outlines, or plane
   neighbours coupling through other cavities. Multi-cavity (PWR sandwiched between two GNDs)
   effects are ignored; only the explicit GND layer is used.
3. **Synthetic geometry** (§2.5): plane height derived as 1.4 × the largest decap distance,
   PAD and decap rows at opposite ends along y, decaps spread evenly across the width; real plane
   outline, component coordinates and orientations are not modelled, so plane capacitance and
   resonance frequencies are those of the synthetic plane. Because the PAD lies only 0.2·D_ref from
   the synthetic plane edge, the edge image increases the PAD–decap spreading inductance by
   typically 10–15 % relative to a large plane (pessimistic). All components on the Top side only.
4. **Dummy caps** share one via set in pairs (§2.6.5); coupling between the two capacitors and the
   extra pad/trace between them are neglected (only the optional per-capacitor mounting inductance).
5. **Via model:** via-pair (two-conductor, image partial-inductance) loop inductance with a single
   user via pitch for all pairs, plus a coaxial anti-pad segment through the nearer plane; mutual
   inductance between parallel via pairs above the planes is neglected (optimistic for
   vias_per_pad > 1 and PAD vias > 1); a mounting inductance of 0 nH (default) is optimistic —
   typical 0402/0603 pad + escape inductance is 0.2–0.6 nH per capacitor; via barrel capacitance,
   stubs below the far plane and vias passing other planes are ignored; the Top-surface end of the
   loop (pads, component body) is not modelled beyond L_mount.
6. **Port model:** square port equivalent (GMD) of the via or via cluster (fixed checkerboard
   arrangement of pitch √2·s_v); error < 1 % for via spacing s ≥ 10·D and ≈ 5 % at s = 2·D; ports
   closer than their widths are unphysical.
7. **Dielectric:** frequency-independent Dk/Df (no Djordjevic–Sarkar dispersion); anisotropy
   ignored.
8. **Conductor:** smooth copper; no surface roughness; skin effect via finite-thickness surface
   impedance; plane thickness taken as-is from stack-up.
9. **Frequency range:** cavity model assumes d ≪ λ and neglects radiation and edge fringing;
   accuracy degrades above a few GHz (warning above 3 GHz; hard cap 20 GHz). Fringing
   capacitance at plane edges is neglected (few % for d/a ≪ 1).
10. **Decap models:** only linear R, L, C, K SPICE subcircuits; no `.INCLUDE`, `.LIB`, behavioural
   sources, frequency-dependent (`LAPLACE`) elements, temperature or DC-bias derating. s2p models
   include their fixture effects; extrapolation outside data is simplistic.
11. **IC side:** no die capacitance or package model; the PAD is an ideal observation port. With
    several PADs (N_pad ≥ 2) the pads are assumed to be joined at an **ideal common node** on the
    die/probe side (zero impedance and equal voltage between the pads, no die/package/bump
    resistance or inductance); current sharing between the pads follows only from the cavity and the
    identical PAD via sets, and mutual inductance between the via sets of different pads is
    neglected. The pads are spread evenly across the plane width at y = 0.2·D_ref, not at the real
    pin-field coordinates, so a compact BGA pin field is better represented by one PAD with more PAD
    vias (cluster port).
12. **Numerics:** f_start ≥ 1 kHz; mode count cap 1500 per axis may under-resolve spreading
    inductance for very large planes with very small drills (warning emitted).
13. **Persistence:** only one running instance auto-saves; computed results are not stored and are
    recomputed on restore.

---

## 10. References

Physical models and formulas are grounded in the following public literature. Tags are used in
the text above.

* **[Okoshi85]** T. Okoshi, *Planar Circuits for Microwaves and Lightwaves*, Springer-Verlag, 1985.
  — planar-circuit (2-D Helmholtz, magnetic-wall) theory and modal Green's function for rectangular
  planar circuits.
* **[Lei99]** G.-T. Lei, R. W. Techentin, B. K. Gilbert, "High-frequency characterization of
  power/ground-plane structures," *IEEE Trans. Microwave Theory Tech.*, vol. 47, no. 5,
  pp. 562–569, 1999. — cavity-model port impedance double sum with χ_mn and sinc port factors;
  lossy wavenumber with tanδ and skin depth.
* **[Kim01]** J.-H. Kim, M. Swaminathan, "Modeling of irregular shaped power distribution planes
  using transmission matrix method," *IEEE Trans. Advanced Packaging*, vol. 24, no. 3,
  pp. 334–346, 2001. — per-unit-cell series impedance/shunt admittance of plane pairs including
  conductor and dielectric loss.
* **[Swaminathan07]** M. Swaminathan, A. E. Engin, *Power Integrity Modeling and Design for
  Semiconductors and Systems*, Prentice Hall, 2007. — cavity resonator model, via inductance,
  port reduction for PDN analysis.
* **[Novak07]** I. Novak, J. R. Miller, *Frequency-Domain Characterization of Power Distribution
  Networks*, Artech House, 2007. — plane-pair impedance, decoupling and measurement methods
  (including two-port shunt-through).
* **[Novak00]** I. Novak, "Measuring milliohms and picohenrys in power distribution networks,"
  *DesignCon 2000*, 2000. — two-port shunt-through impedance measurement.
* **[Smith99]** L. D. Smith, R. E. Anderson, D. W. Forehand, T. J. Pelc, T. Roy, "Power distribution
  system design methodology and capacitor selection for modern CMOS technology," *IEEE Trans.
  Advanced Packaging*, vol. 22, no. 3, pp. 284–291, 1999. — frequency-domain PDN impedance
  methodology, decap series RLC models.
* **[Pozar12]** D. M. Pozar, *Microwave Engineering*, 4th ed., Wiley, 2012. — ABCD↔S conversion
  (series/shunt elements), parallel-plate line losses, surface resistance.
* **[Ramo94]** S. Ramo, J. R. Whinnery, T. Van Duzer, *Fields and Waves in Communication Electronics*,
  3rd ed., Wiley, 1994. — skin depth, surface impedance of finite-thickness conductors, coaxial
  inductance, layered-dielectric capacitance.
* **[Wheeler42]** H. A. Wheeler, "Formulas for the skin effect," *Proc. IRE*, vol. 30, no. 9,
  pp. 412–424, 1942. — skin depth and skin-effect resistance (background only; the finite-thickness
  surface impedance is from [Ramo94], and the plated-via δ_e rule is an engineering interpolation).
* **[Goldfarb91]** M. E. Goldfarb, R. A. Pucel, "Modeling via hole grounds in microstrip," *IEEE
  Microwave and Guided Wave Letters*, vol. 1, no. 6, pp. 135–137, 1991. — via-hole inductance
  formula.
* **[JKB94]** N. L. Johnson, S. Kotz, N. Balakrishnan, *Continuous Univariate Distributions*, vol. 1,
  2nd ed., Wiley, 1994, ch. 13 (truncated normal distributions). — density, moments and
  sampling of the doubly truncated normal distribution used for the decap distances (§2.5.5).
* **[Acklam03]** P. J. Acklam, "An algorithm for computing the inverse normal cumulative distribution
  function," technical note (web), c. 2003 (rational approximation with relative error < 1.15e-9 and a
  Halley refinement step). — Φ⁻¹ of §2.5.5.
* **[Grover46]** F. W. Grover, *Inductance Calculations: Working Formulas and Tables*, Van Nostrand,
  1946 (Dover reprint). — geometric mean distance of a square area (0.44705 × side), of a circle and
  of groups of conductors; mutual inductance of parallel filaments (partial mutual inductance M_p).
* **[Paul10]** C. R. Paul, *Inductance: Loop and Partial*, Wiley-IEEE Press, 2010. — loop vs partial
  inductance (L_loop = L_p1 + L_p2 − 2M_12), closed-form partial self/mutual inductance of parallel
  conductors, image method, GMD method, return path concepts.
* **[Bogatin18]** E. Bogatin, *Signal and Power Integrity — Simplified*, 3rd ed., Prentice Hall,
  2018. — via and plane-pair inductance rules of thumb, PDN impedance concepts.
* **[Ho75]** C.-W. Ho, A. E. Ruehli, P. A. Brennan, "The modified nodal approach to network
  analysis," *IEEE Trans. Circuits and Systems*, vol. 22, no. 6, pp. 504–509, 1975. — MNA
  formulation.
* **[Vlach94]** J. Vlach, K. Singhal, *Computer Methods for Circuit Analysis and Design*, 2nd ed.,
  Van Nostrand Reinhold, 1994. — MNA stamps for inductors with branch currents and mutual
  inductance.
* **[Nagel75]** L. W. Nagel, "SPICE2: A Computer Program to Simulate Semiconductor Circuits,"
  Memorandum UCB/ERL M520, University of California, Berkeley, 1975. — SPICE netlist conventions,
  scale suffixes, coupled-inductor dot convention.
* **[Touchstone02]** EIA/IBIS Open Forum, *Touchstone File Format Specification, Revision 1.1*,
  2002. — Version 1 option line and 2-port data ordering.
* **[Touchstone09]** IBIS Open Forum, *Touchstone® File Format Specification, Version 2.0*, 2009
  (includes the Version 1.x rules). — option line, data ordering for 2-port files.
* **[QtHTML]** The Qt Company, "Supported HTML Subset," Qt 6 documentation (Rich Text Processing).
  — QTextBrowser HTML/CSS constraints.

---

## Appendix A — Error/warning code index

| Code | Where |
|---|---|
| E_XL_HEADER_NOT_FOUND, E_XL_NUMBER, E_XL_UNIT, E_XL_FORMULA_NO_VALUE, W_XL_DUP_COLUMN | §4.1 |
| E_STACK_*, W_STACK_* | §2.2, §4.2 |
| E_PWR_* (incl. E_PWR_WIDTH_TOO_SMALL, E_PWR_NPADS), W_PAD_CLIPPED, W_PWR_FAR_GND, W_PWR_NO_DECAPS, W_XL_COLUMN_IGNORED | §2.5, §4.3 |
| E_DECAP_FILE_NOT_FOUND, E_DECAP_FILE_TYPE, E_DECAP_DISTANCE, E_DREF_TOO_SMALL, W_DECAP_TOO_CLOSE, W_DECAP_CLIPPED, W_PORT_OVERLAP, I_DUMMY_SINGLE, E_XL_BOOL | §2.5, §2.6.5, §4.4 |
| E_DIST_MODE, E_DIST_SIGMA, E_DIST_SEED, W_DIST_SIGMA_LARGE, I_DIST_SAMPLED | §2.5.5 |
| E_PROJECT_FORMAT, E_PROJECT_NEWER, I_PROJECT_MIGRATED, W_PROJECT_UNKNOWN_KEY | §4.7, §5.8.4 |
| W_AUTOSAVE_CORRUPT, W_AUTOSAVE_RECOVERED_BACKUP, W_AUTOSAVE_NEWER | §5.8.3 |
| E_VIA_ANTIPAD, E_VIA_COUNT, E_VIA_PITCH, W_VIA_PITCH_SMALL, W_VIA_ZERO_LENGTH | §2.6 |
| E_SPICE_*, W_SPICE_*, W_K_UNITY, W_MNA_FLOATING | §2.7.1, §3.7, §4.5 |
| E_S2P_*, W_S2P_* | §2.7.2, §3.8, §4.6 |
| E_SWEEP_RANGE, W_SWEEP_HIGH, W_MODES_CAPPED, E_SINGULAR | §3 |

Each code's message text is defined next to its raise site; Help page `input_*.html` lists user-facing
explanations for every code in its area.

## Appendix B — Computation pipeline per PWR (normative order)

1. Resolve PlanePair (§2.2, exact complex ε̃_eff); resolve via pitch s_v; port widths w_pad (n = PAD
   vias per pad, `pad_via_count`) and w_dec (n = `vias_per_pad`) from the via-cluster GMD rule (§2.4.5).
2. Collect enabled decap rows for the PWR; load/cached DecapModel for each; evaluate Z_decap on
   f_eval = grid ∪ markers.
3. (Once per computation, before step 1 of any net.) In `normal` distance mode draw d_kj for all
   enabled decap rows of the table in table/port order (§2.5.5).
   Derive D_ref and H (normal mode: max d_k + σ), place the N_pad PADs (PAD row) and the decap ports, compute caps per
   port (§2.5, §2.6.5); compute C_plane for W × H; `I_DIST_SAMPLED` in normal mode.
4. Build CavityModel with a = W, b = H (mode counts §3.2, static sums §3.3) and evaluate
   Z_cav(f_eval).
5. Via geometry h_near = z_top(nearer plane), t_near, h_R (§2.6.1); L_loop = L_pair(h_near, s_v) + L_ap
   (§2.6.2); R_loop over h_R (§2.6.3); Z_via,dec, Z_via,pad (§2.6.4).
6. Z_L per decap port = (Z_decap,k + jωL_mount)/c_p + Z_via,dec (§2.6.5).
7. Z_pp,red via batched solve with non-finite / rcond check (§3.6); N_pad = 1: Z_PAD = Z_red + Z_via,pad;
   N_pad ≥ 2: Z_PAD = 1/(1ᵀ(Z_pp,red + Z_via,pad·I)⁻¹1) by a second checked batched solve (§2.8);
   optional Z_plane (same combination on Z_PP).
8. Split f_eval results into grid and marker arrays; fill `PwrResult.info` (incl. `n_pads`).

## Appendix C — Design responses to review (REVIEW-physics.md)

| Item | Severity | Response | Where |
|---|---|---|---|
| F1 via-pair loop uses anti-pad instead of via spacing | MAJOR | **Adopted.** New input `vias.via_pitch_mm` (default 1.0 mm); default model `pair` = image partial-inductance pair + anti-pad segment; coax kept as legacy option. | §1.3, §2.6.2, §4.7, §5.2, §5.5, §6.2, §8.5, §8.11 |
| F2 via length to plane centres | MAJOR | **Adopted**, with one refinement: resistance length h_R = 2·h_near + t_near (the reviewer used 2·h_near), because the continuing via also carries current through the nearer plane's thickness, the same segment whose inductance L_ap is kept. Effect ≤ 0.8 % on the §8.11 values (e.g. VDD_IO 12.37 vs review 12.27 mΩ @1 MHz). | §2.6.1, §2.6.3, §3.7, §8.5 |
| F3 extra via pairs do not reduce cavity spreading inductance | MAJOR | **Adopted** (simple to specify): per-port widths from the via-cluster GMD, square grid of pitch √2·s_v (checkerboard of alternating PWR/GND vias at spacing s_v) instead of a separate PAD pitch input, so the requested single "via pitch" input suffices; M, N from min w_p. Verified: cluster port within 0.6 % of explicit parallel ports. | §2.4.3, §2.4.5, §2.5, §3.2, §3.3, §5.2, §8.3 |
| F4 non-(0,0) terms at DC with finite σ | MINOR | Adopted (wording). | §2.4.3 |
| F5 static-split bound ignores Γ_c | MINOR | Adopted: K_split = 4·k_max from max |k²| over f_eval; bound ≈ 1/240. | §3.2, §3.3 |
| F6 edge image near PAD | MINOR | Adopted (reworded, numbers quoted). | §2.5.4, §9 |
| F7 GP option omits mutual term | MINOR | Adopted: L_loop = 2(L_GP − M_p) + L_ap. | §2.6.2 |
| F8 S2P singularity test / sensitivity | MINOR | Adopted (complex distance; note recommending shunt-through). Default stays series-through as fixed by the user requirement. | §2.7.2 |
| F9 exact complex dielectric combination | NOTE | Adopted (zero cost, exact); first-order form documented; test values updated. | §2.2, §8.2 |
| F10 GMD port width verified | NOTE | Adopted the suggested accuracy statement. | §9 item 6 |
| F11 cavity verified; "1e-9" note | NOTE | Adopted: note corrected to 1e-6 (tanδ floor). | §2.4.3, §8.3 |
| F12 singular solves | NOTE | Adopted: non-finite and rcond < 1e-14 check → `E_SINGULAR`; test added. | §3.6, §8.3 |
| F13 all numbers reproduced | NOTE | Acknowledged; §8.5 and §8.11 regenerated for v1.2. | §8 |
| F14 limitations and references | NOTE | Adopted: §9 items 5 and 6 rewritten (incl. mounting-inductance warning), Wheeler42 scope corrected, partial mutual inductance cited to Grover46/Paul10, Touchstone 1.1 added. | §9, §10 |
| F15 DC asymptote verified | NOTE | Acknowledged, no change. | — |
| F16 dummy-cap topology verified | NOTE | Acknowledged; inter-pair mutual inductance remains a documented limitation (optimistic). | §9 item 5 |

No review item is rejected.

## Appendix D — Implementation notes (v0.1.0, from the code review)

Deviations and clarifications found while reviewing the implementation against this document
(details in `docs/REVIEW-code.md`). The normative text above is unchanged.

1. **§3.1 markers.** Implemented exactly as specified: Z is evaluated on the union of the grid and
   the in-range marker frequencies and the marker readouts are the exact values (no interpolation).
   Markers within rel 1e-12 of a grid point reuse that point. The readout table shows `n/a` for a
   marker outside the sweep.
2. **§8.4 #5.** The first x of sub-row 2 is m_x + 0.5·L_x/32 = **1.233350 mm** (the printed
   1.233355 mm is a typo); the tests use 1.233350 mm.
3. **Additional issue codes** (not in Appendix A): `E_DECAP_COUNT`, `E_DECAP_FILE_READ` (model file
   exists but cannot be read), `E_PWR_INTERNAL` (unexpected exception while computing one PWR; the
   other PWRs still compute), `E_INTERNAL` (uncaught GUI exception, reported in the Messages dock),
   `E_VIA_DRILL`, `E_VIA_MODEL`, `E_VIA_PLATING`, `E_VIA_SIGMA`, `E_VIA_MOUNT_L`, `E_XL_OPEN`,
   `E_XL_READ`, `E_XL_MISSING_VALUE`, `E_XL_MODE`, `E_S2P_MODE`, `E_SPICE_SYNTAX`, `E_SPICE_VALUE`,
   `E_SPICE_K`, `W_SPICE_NEGATIVE_R`, `E_PWR_FAILED` (net not computed because of an error whose source is a file, §4.8), `W_EXPORT_SKIPPED` (§4.8), `W_DECAP_PWR_UNKNOWN`, `W_PROJECT_VALUE`,
   `I_PROJECT_SESSION_IGNORED`, and the GUI-only `E_ENGINE_UNAVAILABLE`, `E_INPUTS`, `E_VALIDATION`,
   `E_COMPUTE_FAILED`, `I_COMPUTE_CANCELLED`.
4. **§4.1 Excel reading** is more tolerant than specified: the workbook is opened with
   `read_only=False` so that merged cell ranges can be filled with their top-left value; if the
   keyword/active sheet has no header row the remaining sheets are tried; Dk/Df headers with a
   frequency qualifier (`Dk@1GHz`, `Df 10GHz`) match; the length units `mils`, `thou`, `micron(s)`
   and `inches` are accepted in addition to the listed ones.
5. **§4.4 path resolution** is applied identically in the GUI table and in the engine:
   `ProjectInputs` carries `decap_source_dir` (folder of the decap Excel file) as the first relative
   candidate.
6. **§4.7** Project and auto-save files with a UTF-8 BOM are accepted.
7. **§5.7 logging / uncaught exceptions.** The log file is `<auto-save folder>/logs/app.log`
   (`%APPDATA%\SimplePICalculator\logs` on Windows, not `%LOCALAPPDATA%`). Uncaught exceptions
   (including exceptions raised in Qt slots) are logged and reported non-modally as `E_INTERNAL` in
   the Messages dock and status bar instead of a modal message box, which could re-trigger a failing
   paint/timer slot.
8. **§5.4** Edits made while a computation runs mark the returned results stale
   ("(inputs changed)"), because the worker computed a snapshot of the previous inputs.
9. **§7.1 / §7.4 packaging.** `collect_submodules("pyqtgraph")` is filtered to skip
   `pyqtgraph.examples` (importing it starts a QApplication and aborts or hangs the collector),
   `opengl` and `jupyter`; the package's own modules are listed explicitly because the engine is
   imported lazily. `--self-test` additionally checks every help page and image, the icon and the
   examples folder, computes the example project, creates the main window (offscreen-safe) and
   accepts `--self-test-report PATH`, because the windowed (`console=False`) executable has no
   stdout; CI waits with `WaitForExit(120000)` and prints the report file. The installer file name
   is `SimplePICalculator-Setup-<version>.exe` (README/RELEASING) rather than §7.2's
   `…-<version>-win64-setup`. The Windows workflow's test job runs on Windows only; Ubuntu tests run
   in `ci.yml`.
10. **§3.9 performance architecture (post-v0.1.0).** Static sums grouped by distinct port-factor
    rows, dynamic sum as real GEMMs against a (L, P²) table, frequency-chunked Z assembly and Schur
    reduction on worker threads, PWR nets computed concurrently, BLAS pinned to one thread during a
    computation, and cavity Z-matrix / decap impedance caches. The §3.3 loop is kept as
    `cavity.z_matrix_reference` (tests only). Golden values are unchanged (example project:
    max relative deviation 7.3e-11 vs `tests/data/golden_example.json`). New project key
    `advanced.workers` (§4.7), no schema version change (optional key with default).
11. **§3.6 rcond screening (post-v0.1.0).** The full batched SVD is replaced by a probe estimate
    solved in the same LU call plus an exact SVD at frequencies with estimate < 1e-10 and at the 8
    smallest estimates. `E_SINGULAR` decisions, reported rcond and first offending frequency use
    exact SVD values; `min_rcond` is unchanged on all tested cases. Large-MLO benchmark: 0.45 s →
    0.18–0.22 s (2 workers).
12. **Dummy Cap bug report (post-v0.1.0).** The engine and the table models were correct; inputs
    could be lost in the GUI: (a) Run (F5, menu, toolbar), Save and close did not commit an open
    cell editor or a spin box with keyboard tracking off, so a typed count was ignored although
    visible; (b) Qt's check-box cell toggles only when the 14-px indicator is hit, and a double
    click did not reliably toggle once. Fixed by `MainWindow.commit_pending_edits` and
    `CheckBoxDelegate` (§5.5). Physics check of the report (VDD_CORE, counts ×2): 10 MHz reads
    18.13 mΩ with Dummy Cap vs 23.15 mΩ without because 10 MHz lies just above the anti-resonance
    between the 10 µF bank and the 100 nF bank; halving the via sets raises the bank-to-bank loop
    inductance by ≈ 20 % and moves the peak from 9.66 to 8.81 MHz at nearly the same height
    (23.6 vs 23.8 mΩ). No sub-row splitting occurs (L_x/P ≫ w). With identical port positions the
    §2.6.5 load rule is reproduced exactly (`test_dummy_load_rule_with_identical_positions`).
13. **Schema 2, `vias_per_pad` (§2.6.4, §4.7).** "Vias per decap pad" replaces "Vias per decap"
    (even total). Engine behaviour for n_pad = vias_per_decap/2 is unchanged, so the §8 golden
    values (1 PWR + 1 GND via per decap = `vias_per_pad = 1`) are unchanged (max. rel. deviation
    < 1e-6 vs `tests/data/golden_example.json`).
14. **Schema 3, several observation PADs per PWR net (§2.5.1, §2.8, §4.3, §4.7).** New PWR list
    column `Number of PADs` (`PwrRow.n_pads`, `PwrSpec.n_pads`, `pwr.rows[].n_pads`, GUI column
    `# PADs`), placed as a PAD row with the decap-row rule; each pad has its own `pad_via_count`
    via set ("PAD vias = vias per observation pad"); the pads are combined in parallel at an ideal
    common node. `migrate_2_to_3` adds `n_pads: 1`; frozen fixture `tests/data/project_v2.spical.json`.
    N_pad = 1 keeps the v1.3 coordinates and code path, so the §8 golden values are unchanged (rel
    1e-12 vs the previous result). New codes `E_PWR_NPADS`, `W_PAD_CLIPPED`. Deviations/clarifications:
    the `E_PWR_WIDTH_TOO_SMALL` span check for the PAD row applies only for N_pad ≥ 2 (a single PAD
    keeps the v1.3 rule w_pad ≤ W); the PAD row may be split into sub-rows centred on y_0, and
    "Distance to PAD" is still measured from y_0; the cavity cache key adds N_pad only when N_pad ≠ 1
    (the Z-matrix itself depends only on the port coordinates and widths). N_pad = 4 on the example
    is not equivalent to one pad with 4 PAD vias (425.9 vs 445.7 mΩ @100 MHz for VDD_IO, §8.11).
15. **Schema 4, decap distance distribution (§2.5.5, v0.3.0).** New global option `fixed`/`normal`
    (`core/distribution.py`, `ProjectInputs.distance`, `Project.distance`, `decaps.distance_mode`,
    `sigma_mm`, `seed`; GUI Decaps tab top bar). Fixed mode takes the unchanged code path: the example,
    large-MLO and many-nets benchmark results and all port coordinates are bit-identical to 0.2.0.
    Clarifications: the random stream covers the enabled rows of the whole decap table (not per net)
    so that the preview, single-net and multi-net computations agree; a sampled distance may be ≤ 0
    when σ ≥ d_k (`W_DIST_SIGMA_LARGE`, the port is clipped to the plane edge) instead of being
    rejected; σ and seed are validated only in normal mode; the cavity grouping key was already the
    exact port-factor row, so no code change was needed there (§3.9 note). Test note: σ → 0 matches
    fixed mode linearly in σ (the ports and H move by ≈ σ); at the sharp VDD_IO plane resonance the
    deviation is 1.3e-6 per nm of σ, so the 1e-6 test uses σ = 1e-7 mm.
