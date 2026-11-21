"""A deck that imports only the names it uses, like a hand-written one."""
from manim import RoundedRectangle, Text
from manim_slides import Slide

FONT = "DejaVu Sans"


def card(x, y):
    return RoundedRectangle(width=4, height=2, corner_radius=.18, fill_opacity=1).move_to([x, y, 0])


class Intro(Slide):
    def construct(self):
        self.add(card(3, 0), Text("Explicit imports", font=FONT, font_size=36).move_to([-3, 0, 0]))
        self.next_slide()
