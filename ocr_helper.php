<?php
// ocr_helper.php
// Real OCR integration helper for Tesseract or Google Vision API

class OCRHelper {
    private $tesseractPath;
    private $tempDir;
    private $googleApiKey;

    public function __construct(
        string $tesseractPath = 'tesseract',
        string $tempDir = null,
        string $googleApiKey = null
    ) {
        $this->tesseractPath = $tesseractPath;
        $this->tempDir = $tempDir ?: sys_get_temp_dir();
        $this->googleApiKey = $googleApiKey ?: getenv('GOOGLE_VISION_API_KEY');
    }

    public function extractText(string $imageUrl): array {
        // Try Google Vision API first if available
        if ($this->googleApiKey) {
            try {
                return $this->googleVisionOCR($imageUrl);
            } catch (Exception $e) {
                error_log("Google Vision API failed: " . $e->getMessage());
            }
        }

        // Fallback to Tesseract
        try {
            return $this->tesseractOCR($imageUrl);
        } catch (Exception $e) {
            error_log("Tesseract OCR failed: " . $e->getMessage());
            return [
                'text' => null,
                'confidence' => 0.0,
                'text_candidates' => [],
                'error' => $e->getMessage()
            ];
        }
    }

    private function googleVisionOCR(string $imageUrl): array {
        $client = new \GuzzleHttp\Client();

        // Download image
        $imageData = $client->get($imageUrl)->getBody()->getContents();
        $base64Image = base64_encode($imageData);

        $requestBody = [
            'requests' => [
                [
                    'image' => ['content' => $base64Image],
                    'features' => [
                        ['type' => 'TEXT_DETECTION']
                    ]
                ]
            ]
        ];

        $response = $client->post(
            'https://vision.googleapis.com/v1/images:annotate?key=' . $this->googleApiKey,
            [
                'json' => $requestBody,
                'headers' => ['Content-Type' => 'application/json']
            ]
        );

        $result = json_decode($response->getBody()->getContents(), true);

        if (isset($result['responses'][0]['textAnnotations'][0])) {
            $annotation = $result['responses'][0]['textAnnotations'][0];
            return [
                'text' => $annotation['description'] ?? null,
                'confidence' => $annotation['confidence'] ?? 0.95,
                'text_candidates' => [$annotation['description'] ?? ''],
                'bounding_boxes' => $this->extractBoundingBoxes($result['responses'][0])
            ];
        }

        return [
            'text' => null,
            'confidence' => 0.0,
            'text_candidates' => []
        ];
    }

    private function tesseractOCR(string $imageUrl): array {
        // Download image to temp file
        $client = new \GuzzleHttp\Client();
        $imageData = $client->get($imageUrl)->getBody()->getContents();

        $tempFile = $this->tempDir . '/ocr_image_' . uniqid() . '.jpg';
        file_put_contents($tempFile, $imageData);

        try {
            // Run Tesseract with TSV output for confidence scores
            $command = escapeshellcmd($this->tesseractPath) . ' ' .
                      escapeshellarg($tempFile) . ' stdout --psm 6 -c tessedit_create_tsv=1';

            $output = shell_exec($command);

            if (!$output) {
                throw new Exception("Tesseract returned no output");
            }

            return $this->parseTesseractTSV($output);

        } finally {
            // Clean up temp file
            @unlink($tempFile);
        }
    }

    private function parseTesseractTSV(string $tsvOutput): array {
        $lines = explode("\n", trim($tsvOutput));
        $header = array_shift($lines); // Remove header

        $allText = [];
        $confidences = [];
        $words = [];

        foreach ($lines as $line) {
            $fields = explode("\t", $line);
            if (count($fields) >= 12) {
                $conf = (int)$fields[10];
                $text = trim($fields[11]);

                if ($conf > 0 && !empty($text)) {
                    $allText[] = $text;
                    $confidences[] = $conf;
                    $words[] = [
                        'text' => $text,
                        'confidence' => $conf / 100,
                        'bbox' => [
                            (int)$fields[6], // left
                            (int)$fields[7], // top
                            (int)$fields[8], // width
                            (int)$fields[9]  // height
                        ]
                    ];
                }
            }
        }

        $fullText = implode(' ', $allText);
        $avgConfidence = empty($confidences) ? 0.0 : array_sum($confidences) / count($confidences) / 100;

        return [
            'text' => $fullText ?: null,
            'confidence' => $avgConfidence,
            'text_candidates' => $allText,
            'words' => $words
        ];
    }

    private function extractBoundingBoxes(array $response): array {
        $boxes = [];

        if (isset($response['textAnnotations'])) {
            foreach ($response['textAnnotations'] as $annotation) {
                if (isset($annotation['boundingPoly']['vertices'])) {
                    $vertices = $annotation['boundingPoly']['vertices'];
                    $boxes[] = [
                        'text' => $annotation['description'] ?? '',
                        'bbox' => [
                            $vertices[0]['x'] ?? 0,
                            $vertices[0]['y'] ?? 0,
                            ($vertices[2]['x'] ?? 0) - ($vertices[0]['x'] ?? 0),
                            ($vertices[2]['y'] ?? 0) - ($vertices[0]['y'] ?? 0)
                        ]
                    ];
                }
            }
        }

        return $boxes;
    }

    public function detectStickerTypes(string $text): array {
        $stickers = [];

        // Coupon patterns
        if (preg_match('/\b(\d+%?\s*off|sale|discount|promo|coupon)\b/i', $text, $matches)) {
            $stickers[] = [
                'type' => 'coupon',
                'text' => $matches[0],
                'confidence' => 0.85
            ];
        }

        // Price patterns
        if (preg_match('/[\$€£¥₪]\s*\d+(?:\.\d{2})?|\d+\s*[\$€£¥₪]/', $text, $matches)) {
            $stickers[] = [
                'type' => 'price',
                'text' => $matches[0],
                'confidence' => 0.90
            ];
        }

        // Percentage patterns
        if (preg_match('/\d+\s*%/', $text, $matches)) {
            $stickers[] = [
                'type' => 'percent',
                'text' => $matches[0],
                'confidence' => 0.88
            ];
        }

        // Date patterns
        if (preg_match('/\b\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4}\b/', $text, $matches)) {
            $stickers[] = [
                'type' => 'date',
                'text' => $matches[0],
                'confidence' => 0.80
            ];
        }

        // URL patterns
        if (preg_match('/https?:\/\/[^\s]+|www\.[^\s]+\.com/i', $text, $matches)) {
            $stickers[] = [
                'type' => 'url',
                'text' => $matches[0],
                'confidence' => 0.95
            ];
        }

        return $stickers;
    }
}

// Usage example:
// $ocr = new OCRHelper('/usr/bin/tesseract', '/tmp', 'your-google-api-key');
// $result = $ocr->extractText('https://example.com/image.jpg');
// $stickers = $ocr->detectStickerTypes($result['text']);