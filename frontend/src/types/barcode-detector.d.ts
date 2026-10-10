/**
 * The Barcode Detection API, which TypeScript's DOM library does not describe yet. Not
 * every browser has it; the scanner asks before it uses it and falls back to jsQR.
 */

interface DetectedBarcode {
  readonly rawValue: string;
  readonly format: string;
}

interface BarcodeDetector {
  detect(image: ImageBitmapSource): Promise<DetectedBarcode[]>;
}

interface BarcodeDetectorConstructor {
  new (options?: { formats?: string[] }): BarcodeDetector;
  getSupportedFormats(): Promise<string[]>;
}

interface Window {
  BarcodeDetector?: BarcodeDetectorConstructor;
}
