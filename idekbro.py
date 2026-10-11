import librosa
import numpy as np

# Load audio file
signal, sr = librosa.load("Heyzars.wav", sr=16000)

# Extract 13 MFCCs
mfcc = librosa.feature.mfcc(y=signal, sr=sr, n_mfcc=13)

print("MFCC shape:", mfcc.shape)