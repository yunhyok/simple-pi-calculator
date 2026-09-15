# Physics & Numerics Review of DESIGN.md

Reviewer role: independent signal/power-integrity reviewer. Scope: DESIGN.md §1.2, §2 (all), §3 (all),
§8 expected values, §9, §10, Appendix B. DESIGN.md was not edited.

Numeric evidence comes from an independent numpy re-implementation written from the text of §2–§3,
in the scratchpad `review/` folder: `pdnlib.py` (cavity, via, reduction), `t1_cavity.py` (DC limit,
loop L, port-width rules), `t2_res_split.py` (resonances, static split, truncation), `t3_e2e.py`
(§8.11 end-to-end), `t4_via_impact.py` / `t5_pair.py` (via model alternatives), `t6_cancel.py`
(exact rational-arithmetic Schur check), `t7_padcluster.py` (multi-via PAD port).

Severity scale: **BLOCKER** (wrong result or the spec contradicts itself so tests cannot pass),
**MAJOR** (physically wrong or misleading by tens of percent in the main use case),
**MINOR** (local error or wording, small numeric effect), **NOTE** (verified, or an optional
improvement).

**Summary:** no BLOCKER. 3 MAJOR, 5 MINOR, 8 NOTE. The cavity model, its normalisation, the
lossy-k prefactor rule, the GMD port width, the static split, the S→Z formulas and the Schur
reduction are correct. Every §8 reference number was reproduced to all printed digits. The main
physics problem is the via-loop inductance model (§2.6).

---

## F1. MAJOR: the via-pair loop inductance uses the anti-pad radius instead of the PWR–GND via spacing

**Section:** §2.6.2 (and §2.6.1, §9 item 5, §8.5, §8.11 table).

**Quoted:** "**Decision: default = coaxial (1).** Reasons: (a) the user supplies an anti-pad
diameter, which defines the return-current radius … `L_loop = L'_coax · h_loop` (coax)"
with `L'_coax = (μ0 / 2π) · ln(r_ap / r0)`.

**Problem.** Above the nearer plane, the return current of the PWR via flows in the **GND via**,
not on the anti-pad edge. The anti-pad edge is a ring only 35 µm tall, at the depth of the plane.
The field between the two vias is horizontal, so it is tangential to any planes the vias cross and
is not screened by them. So the loop is a two-conductor loop whose logarithm is ln(s/r0), where s
is the via centre spacing, not ln(r_ap/r0). The document's own cavity check in §2.4.5 uses
(μ0 d/π)·ln(s/r0) for two vias between planes. The coax form is correct only for the short segment
where a via passes through the thickness of a plane it is not connected to. Reason (b), "additive",
also holds for the correct formula. Reason (c) is not relevant to this choice.

The error grows with via length. For short vias (h ≲ s) the coax value is close to the correct one
by coincidence. For VDD_IO (h_near ≈ 1.1 mm) the loop inductance is too low by a factor of 1.6–2.1,
and |Z_PAD| between 10 MHz and 1 GHz is optimistic by 40–480 %.

**Correction (replace the text of §2.6.2 from "Candidate formulas" to "Loop inductance for one via
pair").**

Add an input `vias.via_pitch_mm` = s_v. This is the centre distance between the PWR via and the GND
via of one pair; suggested default 1.0 mm. Validation: s_v > D_drill, else `E_VIA_PITCH`.

```
M_p(l, x) = (μ0/2π)·[ l·ln((l + √(l² + x²))/x) − √(l² + x²) + x ]        [H]   (partial mutual
            inductance of two parallel filaments of length l at distance x; M_p(l, r0) = partial
            self-inductance of a tube of radius r0)                              [Grover46], [Paul10]

L_pair(h, s_v) = M_p(2h, r0) − M_p(2h, s_v)                                 [H]   (two vertical
            barrels of length h ending on the nearer plane, image method; for h ≫ s_v this tends
            to (μ0 h/π)·ln(s_v/r0), the §2.4.5 image result)

L_ap = (μ0/2π)·t_near·ln(r_ap/r0)                                          [H]   (the one via that
            passes through the anti-pad of the nearer plane, over that plane's thickness)

L_loop = L_pair(h_near, s_v) + L_ap                                        (h_near from F2)
```

Keep `via_model = "coax"` only as a legacy or comparison option. Replace the GP option with
`L_loop = 2·(L_GP(h_near) − M_p(h_near, s_v))`, because the partial-inductance loop must subtract
the mutual term. See F7.

**Reference.** [Grover46] mutual inductance of parallel filaments; [Paul10] ch. 5 (loop = ΣL_p − 2M_p,
images); [Bogatin18] loop inductance of a via pair ≈ 10.16·h[in]·ln(s/r) nH, i.e. (μ0 h/π) ln(s/r);
[Novak07] via-pair inductance.

**Evidence** (`t5_pair.py`; D = 0.2 mm, D_ap = 0.5 mm).

| h_near | s_v | image-partial (proposed) | 2-D (μ0h/π)acosh(s/2r0) | doc coax (2h) |
|---|---|---|---|---|
| 0.1525 mm | 0.5 mm | 0.0496 nH | 0.0956 nH | 0.0559 nH |
| 0.1525 mm | 1.0 mm | 0.0585 nH | 0.1398 nH | 0.0559 nH |
| 1.1225 mm | 0.5 mm | 0.6479 nH | 0.7035 nH | 0.4114 nH |
| 1.1225 mm | 1.0 mm | 0.8754 nH | 1.0293 nH | 0.4114 nH |
| 5 mm | 1.0 mm | 4.430 nH | 4.585 nH | 1.833 nH |

Effect on the §8.11 example with F1 and F2 applied (s_v = 1.0 mm), shown as doc value → corrected
value:

| | 100 kHz | 1 MHz | 10 MHz | 100 MHz | 1 GHz |
|---|---|---|---|---|---|
| VDD_CORE (L_loop 0.05589 → 0.0544 nH) | 38.69 → 38.69 mΩ | 3.289 → 3.243 | 35.52 → 35.49 | 140.5 → 139.5 | 2939 → 2903 |
| VDD_IO (L_loop 0.4114 → 0.8660 nH) | 152.5 → 152.0 mΩ | 13.61 → 12.27 | 28.86 → **62.68** | 495.0 → **881.7** | 778.9 → **4494** |

The §8.5 and §8.11 golden values must be regenerated if the fix is adopted.

---

## F2. MAJOR: the via length is measured to plane centres, which contradicts the zero-length test and misplaces the loop boundary

**Section:** §2.6.1, §3.7 item 10, §8.5, §8.11.

**Quoted:** "`h_P = z_c(p)` … `h_loop = h_P + h_G − h_cav` (= 2·min(h_P, h_G)) … If h_loop < 1 µm
(e.g. the nearer plane is the Top metal layer itself) treat as 0 and emit `W_VIA_ZERO_LENGTH`."
§8.5: "PWR 3/GND 1 (plane on Top metal) → 0 + `W_VIA_ZERO_LENGTH`".

**Problem.**

1. **The test cannot pass.** With z_c(1) = t_1/2 = 17.5 µm, the formula gives h_loop = 35 µm, not
   < 1 µm, so the §8.5 case cannot pass as written.
2. **The loop boundary is in the wrong place.** Above 5 MHz a 35 µm plane is thicker than the skin
   depth, so the flux of the via-pair loop is bounded by the **surface of the nearer plane facing
   the components**. Inside that plane's thickness the only open area is the anti-pad (coax, L_ap
   in F1). Below it, the cavity port covers the dielectric gap d and nothing else, because Γ_c
   carries the plane's internal impedance. The centre convention counts t_near/2 twice in the pair
   length and uses the pair formula for it.

**Correction (replace the §2.6.1 code block and the sentence after it).**

```
n       = min(p, g)                     nearer plane (Top mounting)
h_near  = z_top(n)                      [m]  Top surface → component-side face of the nearer plane
t_near  = t_n                           thickness of the nearer plane (anti-pad segment, F1)
h_loop  = 2·h_near                      [m]  used for R_loop (both barrels)
```

If h_near < 1 µm (the nearer plane is layer 1), set L_pair = R_loop = 0 and emit
`W_VIA_ZERO_LENGTH`; L_ap is kept. Example stack-up: PWR 5/GND 3 → h_near = 0.135 mm
(h_loop 0.270 mm); PWR 7/GND 9 → h_near = z_top(7) = 1.105 mm (h_loop 2.210 mm); PWR 3/GND 1 → 0.

**Reference.** [Ramo94] skin-depth screening by thick conductors; [Paul10] loop definition; §2.4.1
(Γ_c already contains the plane metal).

**Evidence.** z_c(1) = 0.0175 mm gives h_loop = 0.035 mm, which is not < 1 µm. Recomputed §8
effect: see the F1 table (computed with h_near = z_top).

---

## F3. MAJOR: extra via pairs at the PAD or decap reduce only the via impedance, not the cavity spreading inductance

**Section:** §2.6.4, §2.4.5 ("All ports use the same w"), §2.8, §9 item 6.

**Quoted:** "`Z_via,pad(ω) = Z_viapair(ω) / n_pad`" … "All ports use the same w (all vias share one
drill diameter)."

**Problem.** With n_pad > 1 (typical for a BGA power pin field) the PAD cavity port stays a
single-via port of width w. The spreading inductance in the cavity near the PAD is usually the
largest mid-frequency term, and the n parallel barrels reduce it strongly. The GUI input
therefore has only part of its physical effect, and a user who adds PAD vias sees too little
improvement. The same applies to `vias_per_decap = 4, 6, …`.

**Correction.**

1. Give each port p its own width, set from the self-GMD of its via cluster (same GMD argument as
   §2.4.5, extended to n conductors):

   ```
   ln g_p = (1/n_p²)·Σ_i Σ_j ln d_ij ,   d_ii = r0 ,   d_ij = centre distance of PWR vias i, j
   w_p    = g_p / 0.44705
   ```

2. Change the §2.4.3 factor `S_m² S_n²` to `S_m,i S_n,i S_m,j S_n,j` with
   `S_m,i = sinc_u(mπ w_i/(2a))`.
3. The PWR vias at the PAD need a spacing input. Add `vias.pad_via_pitch_mm`, assume a square
   array, and apply the same rule to decap via sets with pitch s_v.
4. Take §3.2 M, N from min_p w_p.

If this is out of scope for v1, state in §2.6.4 and §9 item 6 that "additional PAD/decap via pairs
reduce only the via-barrel impedance; the cavity spreading inductance is that of a single via
(pessimistic)".

**Reference.** [Grover46] GMD of a group of conductors; [Paul10]; [Swaminathan07] multi-via ports.

**Evidence** (`t7_padcluster.py`: VDD_IO plane 30×14 mm, PEC, 4 PAD vias in a 1 mm square around
(15, 2) mm, shorted decap port at (15, 12) mm, 1 MHz).

| Model | Cavity loop L |
|---|---|
| Explicit 4 ports in parallel (reference) | 0.1722 nH |
| Doc: single port w = 1.118·D | 0.2090 nH (+21 %) |
| GMD cluster port for PAD (w_p = 1.372 mm), single port for decap | 0.1728 nH (+0.3 %) |

---

## F4. MINOR: with finite conductivity the non-(0,0) terms tend to the plane spreading resistance, not to zero

**Section:** §2.4.3, normalisation check.

**Quoted:** "All other terms are proportional to ω → 0 (inductive)."

**Correction:**

> "All other terms are proportional to Z_p(ω); for σ = ∞ they vanish ∝ ω (inductive), for finite σ
> they tend to the real DC spreading resistance of the plane metal (Z_p → 1/(σt_P) + 1/(σt_G)).
> Both are negligible against 1/(ωC_plane) at low frequency."

**Evidence.** 20×20 mm, Cu 35 µm, centre port. Z_00 − 1/(jωC(1 − j·1e-6)) = 0.6248 mΩ +
j6.2e-7 Ω at 1 kHz, and 0.6248 mΩ at 10 Hz. The design decision itself is confirmed: the bare
jωμ0 d prefactor with lossy k gives a spurious 900.3 Ω real part at 1 kHz for the (0,0) term.

---

## F5. MINOR: the static-split bound ignores Γ_c; the algebra and convergence are correct

**Section:** §3.2, §3.3.

**Quoted:** "For (m,n) ∈ 𝓗, |k²/k_mn²| ≤ 1/16 over the sweep" and "`K_split = 4 · k0_max`".

**Problem.** |k²| = k0²·|(1 − j tanδ)·Γ_c| exceeds k0² when there is conductor loss. The bound is
violated slightly, and more so for thin dielectrics: max|k²|/K_split² = 0.0638 for d = 100 µm with
Cu 35 µm, 0.0679 for d = 25 µm with Cu 12 µm, and 0.0767 for d = 10 µm with Cu 15 µm. The effect
on the result is negligible, but the stated guarantee is false.

**Correction:**

```
k_max   = max over f_eval of |k(ω)| = sqrt(max |k²(ω)|)   (evaluate on the sweep; ≥ k0_max)
K_split = 4 · k_max
```

In §3.2 use k_max in place of k0_max. Also correct "relative error of dropped part ≤ 1/256" to
"≤ (1/16)²/(1 − 1/16) ≈ 1/240".

**Evidence.** Brute-force full dynamic sum against the split (20×20 mm, 3 ports, Cu, tanδ 0.02,
same M, N):

| f | max relative error, Z entries | relative error, loop Z |
|---|---|---|
| 1 kHz | 5.5e-17 | 2.8e-8 |
| 1 MHz | 7.7e-16 | 1.9e-10 |
| 100 MHz | 5.9e-11 | 6.6e-9 |
| 1 GHz | 1.25e-4 | 6.0e-5 |

Dropping S1 raises the 1 GHz loop error to 2.9e-3, so S1 should be kept. The §8.3 #8 tolerance
(rel 1e-3) is met. Truncation check (60×21 mm, D = 0.2 mm, PAD–decap loop L) with 3a/w, 6a/w and
12a/w modes: 0.18375, 0.18386, 0.18387 nH. No simpler alternative is needed. The split is sound.

---

## F6. MINOR: the synthetic plane edge next to the PAD raises the spreading inductance

**Section:** §2.5.4 (third bullet), §9 item 3.

**Quoted:** "The spreading inductance between PAD and decaps … which dominate the mid-frequency
impedance, are preserved."

**Problem.** The PAD sits 0.2·D_ref from a magnetic-wall edge. The in-phase image increases the
PAD self-term.

**Correction:**

> "…are approximately preserved; because the PAD lies only 0.2·D_ref from the synthetic plane edge,
> the edge image increases the PAD–decap spreading inductance by typically 10–15 % relative to a
> large plane (pessimistic)."

Add the same sentence to §9 item 3.

**Evidence.** VDD_IO geometry, PAD–decap separation 10 mm, PEC, 1 MHz:

| Plane | Loop L |
|---|---|
| 30×14 mm (synthetic) | 0.2090 nH |
| 60×60 mm | 0.1860 nH |
| 200×200 mm | 0.1843 nH |
| Infinite-plane image result | 0.1842 nH |

---

## F7. MINOR: the Goldfarb–Pucel option omits the mutual partial inductance

**Section:** §2.6.2.

**Quoted:** "giving L_loop = 2·L_GP(h_near)".

**Problem.** Loop inductance = L_p1 + L_p2 − 2M_12 [Paul10]. Leaving out M_12 overestimates long
loops (h = 5 mm, s = 1 mm: 6.27 nH against 4.43 nH image-partial). For short vias the result
depends on an isolated-post assumption that does not hold here. The claim that GP "double-counts
return-path effects" is not the right justification.

**Correction:** `L_loop = 2·(L_GP(h_near) − M_p(h_near, s_v))`, with M_p from F1.

**Evidence.** h = 1.1225 mm, s = 1 mm: 2·L_GP = 0.782 nH, 2(L_GP − M_p) = 0.550 nH, image-partial
0.875 nH.

---

## F8. MINOR: S2P singularity test should use complex distance; series-through has poor sensitivity for mΩ parts

**Section:** §2.7.2.

**Quoted:** "If |S21| is within 1e-12 of 0 (series) or of 1 (shunt), clamp …"

**Correction:**

> "If |S21| < 1e-12 (series) or |1 − S21| < 1e-12 (shunt), clamp and emit `W_S2P_SINGULAR`."

Add a NOTE to the text: for |Z| ≪ Z0 the series-through form Z = 2Z0(1 − S21)/S21 depends on
1 − S21 ≈ Z/(2Z0) = 5e-5 at 5 mΩ, which is below typical VNA |S21| uncertainty (~1e-3). Shunt-through
is the preferred fixture for decaps near and above SRF [Novak00]. The formulas themselves are
correct (F12).

---

## F9. NOTE: the dielectric stack combination is correct; an exact complex form is available

**Section:** §2.2.

Series εr_eff and the t/εr-weighted tanδ are the correct first-order result [Ramo94]. The exact
complex form costs nothing and could replace it:

```
εr_eff·(1 − j tanδ_eff) = d_d / Σ_{I_d} t_i/(εr_i (1 − j tanδ_i))
```

**Evidence.**

| Case | Exact | Doc (first order) |
|---|---|---|
| Worked example | εr 3.733426, tanδ 0.0146659 | εr 3.733333, tanδ 0.0146667 |
| Extreme mix (εr 3/4.5, tanδ 0.002/0.03) | εr 3.60068, tanδ 0.013194 | εr 3.60000, tanδ 0.013200 |

The difference is below 0.1 % in both cases, so no change is required.

---

## F10. NOTE: GMD equivalent port width verified

**Section:** §2.4.5.

w = r0/0.44705 = 2.2369·r0 is correct. Uniform-current, area-averaged modal self-term =
ln(self-GMD of the square) = ln(0.44705·w) [Grover46], and the self-GMD of a thin ring is r0.

**Evidence** (100×100 mm, PEC, 6a/w modes, reference (μ0d/π)·acosh(s/D)):

| D | s | GMD rule | Conformal radius | Equal area |
|---|---|---|---|---|
| 0.2 mm | 1 mm | +0.44 % | +12.6 % | +10.6 % |
| 0.2 mm | 2 mm | +0.09 % | | |
| 0.2 mm | 10 mm | +0.37 % | | |
| 0.5 mm | 1 mm | +5.3 % | +26 % | +23 % |
| 0.5 mm | 10 mm | +0.48 % (0.14823 nH) | +8.0 % | +6.8 % |

The §2.4.5 prototype figure (0.1482 nH) and §8.3 #6 (0.1826 nH for Cu 35 µm) were reproduced
exactly. The "d + 2t/3" thin-plane estimate is 0.1820 nH. Suggested addition to §9 item 6: "error
< 1 % for s ≥ 10·D, ≈ 5 % at s = 2·D".

---

## F11. NOTE: cavity formula, normalisation, lossy k and resonances verified

**Section:** §2.4.1–§2.4.4.

- The Neumann eigenfunction normalisation (2/a for m ≥ 1) gives C_m·C_n = χ_mn² [Lei99]. The
  prefactor Z_p/(ab) with a pole at k_mn² − k² follows from ∇²V + k²V = −Z_p J_z [Okoshi85].
- The sinc argument `np.sinc(m*w/(2a))` is correct.
- Γ_c = 1 + (1−j)(δ_P+δ_G)/(2d) for thick metal agrees with Lei99's k = ω√(με)·√(1 − j(tanδ + δ/d)).
  The Z_s = η·coth(γt) limits are correct (Im Z_s/(ωμ0) = 11.64 µm against t/3 = 11.67 µm at
  1 MHz, Cu 35 µm).
- DC limit: |Z·jωC − 1| = 1.000e-6 at 1 kHz and 10 kHz. This is exactly the tanδ floor of §3.4; the
  imaginary part agrees to 1e-12. The §8.3 #1 note "verified prototype: 1e-9" should read "1e-6
  (tanδ floor)". The rel 1e-4 tolerance still holds. With tanδ = 0.02 the error is 4.5e-13.
- Resonances: peaks at 749.5 and 937.0 MHz (analytic 749.481 and 936.851 MHz). A port on the
  x = a/2 nodal line shows only 937.0 MHz. f_mn for 20×20 mm (3.7474, 5.2996 GHz) and 60×21 mm
  (1.2048, 3.4422 GHz) agree.

---

## F12. NOTE: S→Z fixture formulas and Schur reduction verified; singular solves need an explicit check

**Section:** §2.7.2, §2.8, §3.6, §3.7.

**S→Z formulas.** Derived from ABCD with S21 = 2/(A + B/Z0 + C·Z0 + D):

| Fixture | S21 | Z from S21 |
|---|---|---|
| Series [[1,Z],[0,1]] | 2Z0/(2Z0+Z) | Z = 2Z0(1−S21)/S21 |
| Shunt [[1,0],[1/Z,1]] | 2Z/(2Z+Z0) | Z = Z0·S21/(2(1−S21)) |

Both match the doc. The rejection of "2·Z0·S21/(1−S21)" is correct.

**Schur reduction.** V_K = −diag(Z_L)·I_K gives Z_red = z00 − z_0Kᵀ(Z_KK+Z_L)⁻¹z_K0, and Z_PAD adds
Z_via,pad in series. Signs and orientation are correct.

**Round-off.** `t6_cancel.py` compares float64 LU against exact rational arithmetic on the VDD_CORE
matrices:

| f | Absolute error | Relative error | eps·\|z00\| (doc bound) |
|---|---|---|---|
| 1 kHz | 1.6e-11 Ω | 4e-12 | 7e-11 Ω |
| 100 kHz | 6.9e-13 Ω | 1.8e-11 | |
| 2.25 MHz | 1.5e-14 Ω | 3.9e-12 | |
| 100 MHz | 7e-17 Ω | 6.7e-16 | |

The §3.7 item 1 claim holds.

**Pitfall.** `numpy.linalg.solve` raises `LinAlgError` only for exactly singular pivots. A
near-singular case, for example coincident ports (`W_PORT_OVERLAP`) with Z_L → 0 (zero via length
plus an ideal short model), returns inf/nan or garbage silently. Add to §3.6: "after the solve, if
any Z_red is non-finite or an rcond estimate < 1e-14 → `E_SINGULAR`".

---

## F13. NOTE: every §2/§8 reference number reproduced

**Sections:** §2.2, §2.3, §2.4, §2.5.3, §2.6, §2.7.1, §2.8, §8.2–§8.5, §8.7, §8.11.

- **§2.2:** εr_eff 3.73333, tanδ 0.0146667.
- **§2.3, §8.2 plane capacitance:** 141.667 pF (1123.44 Ω at 1 MHz), 479.72 pF, 159.91 pF. The
  §8.11 #2 value is 49.58 pF.
- **§2.4.2:** δ_Cu(1 MHz) = 66.085 µm.
- **§2.6.2, §8.5 via inductance:** L_coax(1 mm) = 0.183258 nH; L_GP = 0.328148 nH; L_loop =
  0.055894 and 0.411415 nH.
- **§2.6.3 via resistance:** 1.2544, 1.3369, 4.8665 and 13.826 mΩ.
- **§2.7.1 SRFs:** 23.73 MHz and 2.2508 MHz.
- **§3.2, §8.3 #9, §8.11 mode counts:** 269; 1342; 805×282 with L = 6; 403×188 with L = 2.
- **§2.5.3, §8.4 #1, #3–#7, #9:** coordinates and counts reproduced.
- **§8.11 #4 end-to-end** (my implementation of the doc as written):

  | | 100 kHz | 1 MHz | 10 MHz | 100 MHz | 1 GHz | Plane-only @ 1 MHz |
  |---|---|---|---|---|---|---|
  | VDD_CORE | 38.69 mΩ | 3.289 mΩ | 35.52 mΩ | 140.5 mΩ | 2939 mΩ | 331.7 Ω |
  | VDD_IO | 152.5 mΩ | 13.61 mΩ | 28.86 mΩ | 495.0 mΩ | 778.9 mΩ | 995.1 Ω |

  All agree with §8.11 to every printed digit. Analytic low-frequency values: 38.818 and
  153.03 mΩ.

These values are internally consistent, but they change if F1–F3 are adopted (see the F1 table).

---

## F14. NOTE: limitations and references to add or fix

**Section:** §9, §10.

**§9 item 5:**
- Replace "coaxial barrel inductance with antipad return" with "via-pair (two-conductor) loop
  inductance with user via pitch" (F1).
- Add: "a mounting inductance of 0 nH (default) is optimistic; typical 0402/0603 pad + escape
  inductance is 0.2–0.6 nH per capacitor."

**§9 item 6:** add the F3 statement if multi-via ports are not implemented.

**§9 item 3:** add the F6 edge-image statement.

**§10:**
- [Wheeler42] is not the source of Z_s = η·coth(γt) or of the plated-via δ_e interpolation. Cite
  [Ramo94] for the finite-thickness surface impedance and call the δ_e rule an engineering
  interpolation.
- Add the partial-mutual-inductance formula to the [Grover46] and [Paul10] entries.
- Optionally add the Touchstone v1.1 spec (EIA/IBIS Open Forum, 2002) for the v1 rules.
- The bibliographic data of the other entries (Lei99 T-MTT 47(5):562–569; Kim01 T-AdvP 24(3):334–346;
  Goldfarb91 MGWL 1(6):135–137; Smith99 T-AdvP 22(3):284–291; Ho75 T-CAS 22(6):504–509; Wheeler42
  Proc. IRE 30(9):412–424) is correct.

**Appendix B:** after F1–F3, step 1 should also resolve s_v and the per-port w_p. Step 5 should use
h_near = z_top(nearer plane).

---

## F15. NOTE: DC singularity and asymptote verified

**Section:** §2.8 low-frequency asymptote, §3.4.

- The Z_PAD → 1/(jωC_total) asymptote is confirmed: 38.69 mΩ against 38.82 mΩ at 100 kHz. The
  0.3 % shortfall is the series ESL/spreading reactance, which is physical.
- The tanδ floor makes an exact lossless resonance finite.
- The RLEAK path (100 MΩ) in the 10 µF model does not affect frequencies ≥ 1 kHz.

---

## F16. NOTE: dummy-cap rule, parallel via division and mounting-inductance placement are consistent

**Section:** §2.6.4, §2.6.5.

- Z_L = (Z_decap + jωL_mount)/c + Z_via,dec is the correct topology for c identical capacitors
  sharing one via set.
- Σc = N holds, and the §8.5 port_loads example [0.6, 0.6, 1.1] Ω is correct.
- Dividing Z_viapair by n_pair while neglecting mutual inductance is optimistic, as stated in §9.
  With F1 this can later be refined using M_p between adjacent pairs.

---

## Verdict

The planar cavity core of the design is sound and well documented:
- the modal Z-matrix, normalisation and lossy-wavenumber prefactor (Γ_c) are correct;
- the GMD port width, quasi-static tail extraction and truncation are correct;
- the dielectric combination, fixture S→Z formulas and Schur reduction are correct;
- my independent implementation reproduces every §8 reference number.

The design should not be frozen with the current via model:
- **F1:** the anti-pad-based coax loop inductance ignores the PWR–GND via spacing. It underestimates
  the loop inductance of long via pairs by about 2× and makes |Z| for layer pairs deep in the stack
  optimistic by up to several times above 10 MHz.
- **F2:** the plane-centre length convention contradicts its own zero-length test.
- **F3:** extra PAD/decap vias do not reduce the cavity spreading inductance.

Adopt F1 and F2 (a new `via_pitch_mm` input, image-partial loop formula, z_top-based length) before
implementation. Then either implement F3 or document it as a limitation, and regenerate the §8.5 and
§8.11 golden values. The MINOR and NOTE items are text and robustness fixes.
