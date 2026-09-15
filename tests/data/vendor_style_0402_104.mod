***********************************************************************
* SYNTHETIC vendor-style MLCC SPICE model -- test fixture only.
* Written for Simple PI Calculator; NOT actual manufacturer data.
* Style mimics public MLCC libraries: header block, .PARAM, nested
* multi-stage R-L ladder (skin effect) with coupled inductors (K),
* dielectric-relaxation branch, insulation resistance.
*
*   Part number : SYN-0402X7R104K
*   Capacitance : 100nF   Rated voltage : 16V   Size : 0402 (1005)
*   Temperature : 25degC  DC bias : 0V
***********************************************************************
.PARAM Cnom=100n
.PARAM Rs0 = 12m   Ls0 = 180p
*
.SUBCKT SYN0402X7R104 port1 port2
X1 port1 n10 ESL_LADDER PARAMS: L1=Ls0 L2={Ls0*0.8}
+ R2=25m L3='Ls0/2' R3=60m
+ KC=0.3
Rs      n10 n20   {Rs0}
C1      n20 n30   {Cnom*0.98}          ; main capacitance
Rdiel   n30 port2 4.5m TC1=0.001
* dielectric relaxation branch
C2      n20 n40   {Cnom*0.02}          $ 2 % slow polarisation
R4      n40 port2 1.2
Rins    port1 port2 5.0e9
.ENDS SYN0402X7R104
*
.SUBCKT ESL_LADDER a b PARAMS: L1=100p L2=100p R2=10m L3=50p R3=20m KC=0.2
La      a  m1  L1
* skin-effect ladder: (Lb || Rb) then (Lc || Rc)
Lb      m1 m2  L2
Rb      m1 m2  R2
Lc      m2 b   L3
Rc      m2 b   R3
Kabc    La Lb Lc KC
.ENDS ESL_LADDER
*
.END
