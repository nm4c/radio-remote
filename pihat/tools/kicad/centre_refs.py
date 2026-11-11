"""Put each footprint's silkscreen reference where its fab-layer reference is, and delete the fab copy.

KiCad's stock footprints show the reference twice: large on the silkscreen outside the part, and
small on the fab layer in its middle. This keeps one, on the silkscreen, in the middle, at the fab
text's size but never smaller than 0.5 mm high and 0.1 mm stroke (the board's min_text_height is
set to match). Footprints without a fab reference keep theirs; T1/T2 are centred on the part.

Run after Update PCB from Schematic adds parts, with KiCad closed (it edits the board file):
    /Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/3.9/bin/python3 centre_refs.py
    ... centre_refs.py ../../../sa818s-hat/sa818s-hat.kicad_pcb      (the SA818S HAT)
"""
import os
import sys

import pcbnew

PCB = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "pihat.kicad_pcb")
MIN_H, MIN_W = pcbnew.FromMM(0.5), pcbnew.FromMM(0.1)
CENTRE = {"T1", "T2"}  # no fab reference in their footprint: centre on the body


def main():
    board = pcbnew.LoadBoard(PCB)
    # Collect first: removing items while iterating upsets KiCad 10's SWIG wrappers.
    plan = []
    for fp in list(board.GetFootprints()):
        fab = [t for t in list(fp.GraphicalItems()) if isinstance(t, pcbnew.PCB_TEXT)
               and t.GetText() == "${REFERENCE}" and t.GetLayer() in (pcbnew.F_Fab, pcbnew.B_Fab)]
        if fab or fp.GetReference() in CENTRE:
            plan.append((fp, fab))
    for fp, fab in plan:
        ref = fp.Reference()
        if fab:
            t = fab[0]
            ref.SetTextHeight(max(t.GetTextHeight(), MIN_H))
            ref.SetTextWidth(max(t.GetTextWidth(), MIN_H))
            ref.SetTextThickness(max(t.GetTextThickness(), MIN_W))
            ref.SetPosition(t.GetPosition())
            ref.SetTextAngle(t.GetTextAngle())
            ref.SetHorizJustify(t.GetHorizJustify())
            ref.SetVertJustify(t.GetVertJustify())
            ref.SetKeepUpright(t.IsKeepUpright())
        else:
            ref.SetPosition(fp.GetPosition())
            ref.SetTextAngle(fp.GetOrientation())
            ref.SetHorizJustify(pcbnew.GR_TEXT_H_ALIGN_CENTER)
            ref.SetVertJustify(pcbnew.GR_TEXT_V_ALIGN_CENTER)
        ref.SetLayer(pcbnew.B_SilkS if fp.IsFlipped() else pcbnew.F_SilkS)
        ref.SetVisible(True)
    for fp, fab in plan:
        for t in fab:
            fp.Remove(t)
    pcbnew.SaveBoard(PCB, board)
    print(f"{sum(1 for _, f in plan if f)} references moved from the fab layer, "
          f"{sum(1 for _, f in plan if not f)} centred")


if __name__ == "__main__":
    main()
