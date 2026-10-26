import cv2, numpy as np, onnxruntime as ort

PERSON, PHONE = 0, 67

class Detector:
    """YOLOv8n via ONNX Runtime (no PyTorch -> fits in 512MB RAM)."""
    def __init__(self, path: str, conf: float = 0.5):
        self.conf = conf
        self.sess = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
        self.inp = self.sess.get_inputs()[0].name

    def detect(self, img):
        h, w = img.shape[:2]
        r = 640 / max(h, w)
        nh, nw = int(h * r), int(w * r)
        canvas = np.full((640, 640, 3), 114, np.uint8)
        canvas[:nh, :nw] = cv2.resize(img, (nw, nh))
        x = canvas[:, :, ::-1].transpose(2, 0, 1)[None].astype(np.float32) / 255
        out = self.sess.run(None, {self.inp: x})[0][0].T  # (8400, 84)
        scores = out[:, 4:]
        cls, conf = scores.argmax(1), scores.max(1)
        m = np.isin(cls, (PERSON, PHONE)) & (conf > self.conf)
        if not m.any():
            return []
        b, c, k = out[m, :4], conf[m], cls[m]
        xywh = np.stack([b[:, 0] - b[:, 2] / 2, b[:, 1] - b[:, 3] / 2, b[:, 2], b[:, 3]], 1)
        keep = np.array(cv2.dnn.NMSBoxes(xywh.tolist(), c.tolist(), self.conf, 0.45)).flatten()
        return [{"cls": int(k[i]), "conf": float(c[i])} for i in keep]
