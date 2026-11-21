from manim import *
from manim_slides import Slide


class Intro(Slide):
    def construct(self):
        circle = Circle()
        label = Text("GeWu slide fixture")
        self.add(circle, label)
        self.next_slide()
