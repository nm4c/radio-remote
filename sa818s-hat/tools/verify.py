"""Check the SA818S HAT's exported netlist against the intended connections."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "kicadgen"))
from netcheck import check, merge  # noqa: E402

PROJ = os.environ.get("SA818S_PROJ") or os.path.join(HERE, "..")
NET = os.path.join(PROJ, "build", "sa818s-hat.net")

ROOT = {
    "+5V": "J1.2 J1.4",
    "GND": "J1.6 J1.9 J1.14 J1.20 J1.25 J1.30 J1.34 J1.39 U1.1 U1.2 U1.3 U1.4 C1.2",
    "ID_SD": "U1.5 R1.2 J1.27",
    "ID_SC": "U1.6 R2.2 J1.28",
    "EE_WP": "U1.7 R3.2 TP1.1",
    "+3V3": "U1.8 R1.1 R2.1 C1.1 R3.1 J1.1 J1.17",
}
POWER = {   # pihat power sheet with supply="pi": codec rails and LED only, 5 V from the Pi
    "+5V": "U4.1 U4.3 C24.1 R7.1 U5.1 U5.3 C26.1",   # U5 (1.8 V digital core) from 5 V, not the analog rail
    "+3.3VA": "U4.5 C25.1", "+1V8": "U5.5 C27.1", "LED": "R7.2 LED1.2",
    "GND": "U4.2 C24.2 C25.2 C26.2 U5.2 C27.2 LED1.1",
}
RFPOWER = {   # TPS7A94 LDO, 5 V -> 4.2 V for the module
    "+5V": "C31.1 C32.1 U7.1 U7.2 R34.1 R32.1",
    "VREG": "U7.9 U7.10 U7.8 C33.1 C35.1 R34.2 FB1.1 R36.1",
    "EN_RF": "U7.3 R32.2 R35.1", "PG_SET": "U7.5 R36.2 R37.1", "NR_SS": "U7.7 R31.1 C34.1",
    "+4V2_RF": "FB1.2 C38.1 C39.1 U6.8 C40.1 C45.1",
    "GND": "C31.2 C32.2 U7.6 U7.11 C33.2 C35.2 C38.2 C39.2 R31.2 C34.2 R35.2 R37.2",
}
TRANSCEIVER = {
    "ANT": "U6.12 C41.1 L3.1", "RF2": "L3.2 C42.1 L4.1", "RF3": "L4.2 C43.1 L5.1", "RF_OUT": "L5.2 C44.1 J3.1 L6.1",
    "SA_MIC": "U6.18 C51.2", "SA_AF": "U6.3 C52.1",
    "SA_RXD": "U6.16 R48.2", "SA_TXD": "U6.17 R46.1",
    "SA_PTT": "U6.5 Q1.3 R40.2", "SA_HL": "U6.7 Q2.3", "SA_AUDIOON": "U6.1 R44.1",
    "GPIO27": "U6.6 R43.1 J1.13", "GPIO17": "Q1.1 R41.1 J1.11", "GPIO22": "Q2.1 R42.1 J1.15",
    "UART_TXD": "R48.1 J1.8", "UART_RXD": "R46.2 R47.1 J1.10", "GPIO23": "R44.2 R45.1 J1.16",
    "+3V3": "R40.1",
    "GND": "C45.2 L6.2 U6.9 U6.10 U6.19 U6.20 C40.2 C41.2 C42.2 C43.2 C44.2 J3.2 Q1.2 Q2.2 R41.2 R42.2 R43.2 R47.2 R45.2",
}
CODEC = {
    "I2C_SCL": "U2.8 J1.5", "I2C_SDA": "U2.9 J1.3", "CODEC_RST": "U2.31 R21.1 J1.37",
    "OSC_12M": "Y1.3 R16.1", "U2_MCLK": "R16.2 U2.1",
    "I2S_BCLK": "R17.1 J1.12", "U2_BCLK": "R17.2 U2.2", "I2S_LRCLK": "R18.1 J1.35", "U2_WCLK": "R18.2 U2.3",
    "I2S_DIN": "R19.1 J1.40", "U2_DIN": "R19.2 U2.4", "I2S_DOUT": "R20.1 J1.38", "U2_DOUT": "R20.2 U2.5",
    "+3V3": "U2.7 Y1.4 Y1.1 C11.1 C14.1", "+1V8": "U2.32 C12.1",
    "+3.3VA": "U2.25 U2.18 U2.24 C8.1 C9.1 C10.1 C13.1",
    "GND": "U2.6 U2.17 U2.26 U2.21 U2.33 R21.2 Y1.2 C8.2 C9.2 C10.2 C11.2 C12.2 C13.2 C14.2",
}
AUDIO = {
    "HP_P": "U2.19 C50.1", "C50_R50": "C50.2 R50.1", "TXPAD": "R50.2 R51.1 C51.1",
    "C52_R52": "C52.2 R52.1", "RX_P": "U2.10 R52.2 R53.1", "RX_N": "U2.11 C53.1",
    "GND": "R51.2 R53.2 C53.2",
}
NC = {"U4.4", "U5.4", "U2.20", "U7.4"} | {f"U6.{n}" for n in (2, 4, 11, 13, 14, 15)} \
     | {f"U2.{n}" for n in (12, 13, 14, 15, 16, 22, 23, 27, 28, 29, 30)}

if __name__ == "__main__":
    sys.exit(check(NET, merge(ROOT, POWER, RFPOWER, TRANSCEIVER, CODEC, AUDIO), NC,
                   named={"+4V2_RF": "U6.8", "GND": "U6.9", "+5V": "J1.2"}))
