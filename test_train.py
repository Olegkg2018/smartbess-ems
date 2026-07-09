import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from src.ml.pipeline import train_models

print("Starting model training test...")
try:
    metrics = train_models()
    print("Training successful! Metrics:")
    print(metrics)
except Exception as e:
    import traceback
    traceback.print_exc()
    print("Training failed with error:", e)
