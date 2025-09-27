<?php
// brand_detector.php
// Advanced brand detection for images and text

class BrandDetector {
    private $brandDatabase;
    private $logoDetectionEnabled;

    public function __construct() {
        $this->brandDatabase = $this->loadBrandDatabase();
        $this->logoDetectionEnabled = extension_loaded('imagick') || extension_loaded('gd');
    }

    private function loadBrandDatabase(): array {
        return [
            'fashion' => [
                'nike' => ['Nike', 'swoosh', 'just do it'],
                'adidas' => ['Adidas', 'three stripes', 'impossible is nothing'],
                'zara' => ['Zara', 'zara home'],
                'h&m' => ['H&M', 'hennes mauritz', 'hennes & mauritz'],
                'uniqlo' => ['Uniqlo', 'lifewear'],
                'shein' => ['SHEIN', 'she in', 'sheinside'],
                'forever21' => ['Forever 21', 'forever 21', 'f21'],
                'mango' => ['Mango', 'mng'],
                'pull&bear' => ['Pull & Bear', 'pull and bear', 'pullandbear'],
                'bershka' => ['Bershka', 'bsk'],
                'stradivarius' => ['Stradivarius', 'strd'],
                'massimo_dutti' => ['Massimo Dutti', 'md'],
                'cos' => ['COS', 'collection of style'],
                'weekday' => ['Weekday', 'wknd'],
                'monki' => ['Monki'],
                'other_stories' => ['& Other Stories', 'other stories'],
            ],
            'beauty' => [
                'sephora' => ['Sephora', 'sepho'],
                'ulta' => ['Ulta', 'ulta beauty'],
                'loreal' => ['L\'Oreal', 'loreal paris'],
                'maybelline' => ['Maybelline', 'maybe she\'s born with it'],
                'revlon' => ['Revlon'],
                'clinique' => ['Clinique'],
                'estee_lauder' => ['Estée Lauder', 'estee lauder'],
                'mac' => ['MAC', 'make-up art cosmetics'],
                'nars' => ['NARS'],
                'urban_decay' => ['Urban Decay'],
            ],
            'tech' => [
                'apple' => ['Apple', 'iphone', 'ipad', 'macbook', 'airpods'],
                'samsung' => ['Samsung', 'galaxy'],
                'google' => ['Google', 'pixel'],
                'microsoft' => ['Microsoft', 'xbox', 'surface'],
                'amazon' => ['Amazon', 'alexa', 'echo'],
                'tesla' => ['Tesla'],
                'sony' => ['Sony', 'playstation'],
                'nintendo' => ['Nintendo', 'switch'],
            ],
            'food' => [
                'mcdonalds' => ['McDonald\'s', 'mcdonalds', 'big mac', 'happy meal'],
                'kfc' => ['KFC', 'kentucky fried chicken'],
                'starbucks' => ['Starbucks', 'frappuccino'],
                'cocacola' => ['Coca-Cola', 'coca cola', 'coke'],
                'pepsi' => ['Pepsi', 'pepsi cola'],
                'nestle' => ['Nestlé', 'nestle'],
                'unilever' => ['Unilever'],
            ],
            'automotive' => [
                'bmw' => ['BMW', 'ultimate driving machine'],
                'mercedes' => ['Mercedes-Benz', 'mercedes', 'the best or nothing'],
                'audi' => ['Audi', 'vorsprung durch technik'],
                'volkswagen' => ['Volkswagen', 'vw'],
                'toyota' => ['Toyota', 'let\'s go places'],
                'honda' => ['Honda'],
                'ford' => ['Ford', 'built tough'],
                'tesla' => ['Tesla'],
            ]
        ];
    }

    public function detectBrands(string $imageUrl, string $text): array {
        $brands = [];

        // Text-based detection
        $textBrands = $this->detectBrandsInText($text);
        $brands = array_merge($brands, $textBrands);

        // Image-based detection (if enabled)
        if ($this->logoDetectionEnabled && !empty($imageUrl)) {
            try {
                $imageBrands = $this->detectLogosInImage($imageUrl);
                $brands = array_merge($brands, $imageBrands);
            } catch (Exception $e) {
                error_log("Logo detection failed: " . $e->getMessage());
            }
        }

        // Remove duplicates and sort by confidence
        $brands = $this->deduplicateBrands($brands);
        usort($brands, fn($a, $b) => $b['confidence'] <=> $a['confidence']);

        return $brands;
    }

    private function detectBrandsInText(string $text): array {
        $brands = [];
        $textLower = mb_strtolower($text, 'UTF-8');

        foreach ($this->brandDatabase as $category => $brandData) {
            foreach ($brandData as $brandKey => $keywords) {
                $brandName = $keywords[0]; // First keyword is the official brand name

                foreach ($keywords as $keyword) {
                    $keywordLower = mb_strtolower($keyword, 'UTF-8');

                    // Exact match
                    if (strpos($textLower, $keywordLower) !== false) {
                        $confidence = $this->calculateTextConfidence($keyword, $text);

                        $brands[] = [
                            'value' => $brandName,
                            'confidence' => $confidence,
                            'method' => 'text',
                            'category' => $category,
                            'matched_keyword' => $keyword
                        ];
                        break; // Found this brand, move to next
                    }
                }
            }
        }

        return $brands;
    }

    private function calculateTextConfidence(string $keyword, string $text): float {
        $keywordLower = mb_strtolower($keyword, 'UTF-8');
        $textLower = mb_strtolower($text, 'UTF-8');

        // Base confidence
        $confidence = 0.75;

        // Boost confidence for exact case match
        if (strpos($text, $keyword) !== false) {
            $confidence += 0.10;
        }

        // Boost confidence for word boundaries
        if (preg_match('/\b' . preg_quote($keywordLower, '/') . '\b/u', $textLower)) {
            $confidence += 0.10;
        }

        // Boost confidence for brand-specific patterns
        if (preg_match('/@' . preg_quote($keywordLower, '/') . '\b/u', $textLower)) {
            $confidence += 0.05; // Mentioned as username
        }

        return min(0.95, $confidence);
    }

    private function detectLogosInImage(string $imageUrl): array {
        // This is a simplified logo detection approach
        // In a real implementation, you would use:
        // 1. Google Vision API Logo Detection
        // 2. AWS Rekognition Celebrity/Logo Recognition
        // 3. Custom trained models
        // 4. Template matching for specific logos

        $brands = [];

        try {
            // Download and analyze image
            $client = new \GuzzleHttp\Client();
            $imageData = $client->get($imageUrl)->getBody()->getContents();

            // Simple color-based detection for well-known brand colors
            $dominantColors = $this->extractDominantColors($imageData);
            $colorBrands = $this->detectBrandsByColor($dominantColors);

            foreach ($colorBrands as $brand) {
                $brands[] = [
                    'value' => $brand['name'],
                    'confidence' => $brand['confidence'],
                    'method' => 'logo',
                    'detection_type' => 'color_analysis'
                ];
            }

        } catch (Exception $e) {
            error_log("Image analysis failed: " . $e->getMessage());
        }

        return $brands;
    }

    private function extractDominantColors(string $imageData): array {
        // Simple dominant color extraction
        $tempFile = tempnam(sys_get_temp_dir(), 'brand_detect_');
        file_put_contents($tempFile, $imageData);

        try {
            if (extension_loaded('imagick')) {
                return $this->extractColorsImageMagick($tempFile);
            } elseif (extension_loaded('gd')) {
                return $this->extractColorsGD($tempFile);
            }
        } finally {
            @unlink($tempFile);
        }

        return [];
    }

    private function extractColorsImageMagick(string $imagePath): array {
        $image = new \Imagick($imagePath);
        $image->scaleImage(100, 100, true); // Reduce for faster processing

        $histogram = $image->getImageHistogram();
        $colors = [];

        foreach ($histogram as $pixel) {
            $rgb = $pixel->getColor();
            $count = $pixel->getColorCount();

            $colors[] = [
                'r' => $rgb['r'],
                'g' => $rgb['g'],
                'b' => $rgb['b'],
                'count' => $count
            ];
        }

        // Sort by frequency and return top colors
        usort($colors, fn($a, $b) => $b['count'] <=> $a['count']);
        return array_slice($colors, 0, 5);
    }

    private function extractColorsGD(string $imagePath): array {
        $image = imagecreatefromstring(file_get_contents($imagePath));
        if (!$image) return [];

        $width = imagesx($image);
        $height = imagesy($image);
        $colors = [];

        // Sample colors from a grid
        for ($x = 0; $x < $width; $x += 10) {
            for ($y = 0; $y < $height; $y += 10) {
                $rgb = imagecolorat($image, $x, $y);
                $r = ($rgb >> 16) & 0xFF;
                $g = ($rgb >> 8) & 0xFF;
                $b = $rgb & 0xFF;

                $colorKey = "$r,$g,$b";
                $colors[$colorKey] = ($colors[$colorKey] ?? 0) + 1;
            }
        }

        imagedestroy($image);

        // Convert to array format
        $result = [];
        foreach ($colors as $colorKey => $count) {
            [$r, $g, $b] = explode(',', $colorKey);
            $result[] = [
                'r' => (int)$r,
                'g' => (int)$g,
                'b' => (int)$b,
                'count' => $count
            ];
        }

        usort($result, fn($a, $b) => $b['count'] <=> $a['count']);
        return array_slice($result, 0, 5);
    }

    private function detectBrandsByColor(array $colors): array {
        $brands = [];

        // Brand color signatures
        $brandColors = [
            'coca-cola' => ['name' => 'Coca-Cola', 'rgb' => [255, 0, 0], 'tolerance' => 50],
            'pepsi' => ['name' => 'Pepsi', 'rgb' => [0, 82, 156], 'tolerance' => 50],
            'starbucks' => ['name' => 'Starbucks', 'rgb' => [0, 112, 74], 'tolerance' => 40],
            'mcdonalds' => ['name' => 'McDonald\'s', 'rgb' => [255, 198, 41], 'tolerance' => 50],
            'facebook' => ['name' => 'Facebook', 'rgb' => [59, 89, 152], 'tolerance' => 30],
            'twitter' => ['name' => 'Twitter', 'rgb' => [29, 161, 242], 'tolerance' => 30],
            'instagram' => ['name' => 'Instagram', 'rgb' => [188, 42, 141], 'tolerance' => 40],
        ];

        foreach ($colors as $color) {
            foreach ($brandColors as $brandKey => $brandColor) {
                $distance = $this->colorDistance(
                    [$color['r'], $color['g'], $color['b']],
                    $brandColor['rgb']
                );

                if ($distance <= $brandColor['tolerance']) {
                    $confidence = max(0.4, 1.0 - ($distance / $brandColor['tolerance']));

                    $brands[] = [
                        'name' => $brandColor['name'],
                        'confidence' => $confidence * 0.7, // Lower confidence for color-only detection
                        'color_match' => true,
                        'color_distance' => $distance
                    ];
                }
            }
        }

        return $brands;
    }

    private function colorDistance(array $color1, array $color2): float {
        // Euclidean distance in RGB space
        return sqrt(
            pow($color1[0] - $color2[0], 2) +
            pow($color1[1] - $color2[1], 2) +
            pow($color1[2] - $color2[2], 2)
        );
    }

    private function deduplicateBrands(array $brands): array {
        $unique = [];
        $seen = [];

        foreach ($brands as $brand) {
            $key = $brand['value'];

            if (!isset($seen[$key])) {
                $seen[$key] = true;
                $unique[] = $brand;
            } else {
                // If we've seen this brand, update with higher confidence
                foreach ($unique as &$existingBrand) {
                    if ($existingBrand['value'] === $key && $brand['confidence'] > $existingBrand['confidence']) {
                        $existingBrand = $brand;
                        break;
                    }
                }
            }
        }

        return $unique;
    }

    public function getBrandCategories(): array {
        return array_keys($this->brandDatabase);
    }

    public function getBrandsByCategory(string $category): array {
        return $this->brandDatabase[$category] ?? [];
    }
}

// Usage example:
// $detector = new BrandDetector();
// $brands = $detector->detectBrands('https://example.com/image.jpg', 'Check out this Nike shoes!');
// print_r($brands);