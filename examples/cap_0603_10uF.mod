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
