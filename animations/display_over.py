import sys
from pathlib import Path

import numpy as np
import sounddevice as sd
from PyQt5.QtCore import Qt, QTimer, QPoint
from PyQt5.QtGui import QPixmap, QCursor
from PyQt5.QtWidgets import QApplication, QLabel

HERE = Path(__file__).parent
THRESHOLD = 0.01
SIZE = 220

rms = [0.0]

def audio(indata, frames, time, status):
    rms[0] = float(np.sqrt(np.mean(indata**2)))

app = QApplication(sys.argv)
lb = QLabel(flags=Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
lb.setAttribute(Qt.WA_TranslucentBackground)
lb.setCursor(QCursor(Qt.PointingHandCursor))

def load(name):
    return QPixmap(str(HERE / name)).scaled(SIZE, SIZE, Qt.KeepAspectRatio, Qt.SmoothTransformation)

idle, listening = load("idle.png"), load("listening.png")
lb.setPixmap(idle)

geo = app.primaryScreen().availableGeometry()
lb.move(geo.right() - lb.width() - 20, geo.bottom() - lb.height() - 20)

def refresh():
    lb.setPixmap(listening if rms[0] > THRESHOLD else idle)

drag = QPoint()

def press(e):
    global drag
    drag = e.globalPos() - lb.pos()

def move(e):
    if e.buttons() & Qt.LeftButton:
        lb.move(e.globalPos() - drag)

def release(e):
    if e.button() == Qt.RightButton:
        app.quit()

lb.mousePressEvent, lb.mouseMoveEvent, lb.mouseReleaseEvent = press, move, release

timer = QTimer()
timer.timeout.connect(refresh)
timer.start(100)

stream = sd.InputStream(channels=1, callback=audio)
stream.start()

lb.show()
sys.exit(app.exec_())
