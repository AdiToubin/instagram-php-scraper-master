<?php
// test_enhanced.php
// Test script for enhanced media extractor

declare(strict_types=1);

require __DIR__ . '/ocr_helper.php';
require __DIR__ . '/brand_detector.php';

echo "=== Enhanced Media Extractor Test Suite ===\n\n";

// Test 1: OCR Helper
echo "1. Testing OCR Helper...\n";
try {
    $ocrHelper = new OCRHelper();
    echo "   ✓ OCRHelper class instantiated successfully\n";

    // Test sticker type detection
    $testText = "Special offer 50% OFF! Visit https://example.com Price: $29.99";
    $stickers = $ocrHelper->detectStickerTypes($testText);
    echo "   ✓ Detected " . count($stickers) . " sticker types in test text\n";

    foreach ($stickers as $sticker) {
        echo "     - {$sticker['type']}: '{$sticker['text']}' (confidence: {$sticker['confidence']})\n";
    }
} catch (Exception $e) {
    echo "   ✗ OCR Helper error: " . $e->getMessage() . "\n";
}

echo "\n";

// Test 2: Brand Detector
echo "2. Testing Brand Detector...\n";
try {
    $brandDetector = new BrandDetector();
    echo "   ✓ BrandDetector class instantiated successfully\n";

    // Test text-based brand detection
    $testText = "Check out these amazing Nike shoes! Also love my new iPhone from Apple.";
    $brands = $brandDetector->detectBrands('', $testText);
    echo "   ✓ Detected " . count($brands) . " brands in test text\n";

    foreach ($brands as $brand) {
        echo "     - {$brand['value']}: {$brand['method']} detection (confidence: {$brand['confidence']})\n";
    }

    // Test categories
    $categories = $brandDetector->getBrandCategories();
    echo "   ✓ Available brand categories: " . implode(', ', $categories) . "\n";
} catch (Exception $e) {
    echo "   ✗ Brand Detector error: " . $e->getMessage() . "\n";
}

echo "\n";

// Test 3: Enhanced Media Extractor Functions
echo "3. Testing Enhanced Media Extractor Functions...\n";

// Include functions from main script
require_once __DIR__ . '/enhanced_media_extractor.php';

// Test language detection
$hebrewText = "שלום עולם זה טקסט בעברית";
$englishText = "Hello world this is English text";
$mixedText = "Check out this Nike product שלום";

$langHe = detectLanguage($hebrewText);
$langEn = detectLanguage($englishText);
$langMixed = detectLanguage($mixedText);

echo "   ✓ Language detection:\n";
echo "     - Hebrew text: " . ($langHe ?? 'null') . "\n";
echo "     - English text: " . ($langEn ?? 'null') . "\n";
echo "     - Mixed text: " . ($langMixed ?? 'null') . "\n";

// Test URL extraction
$textWithUrls = "Visit https://example.com or check link in bio! Also see www.test.com";
$urls = extractUrls($textWithUrls, []);
echo "   ✓ URL extraction found " . count($urls) . " URLs:\n";
foreach ($urls as $url) {
    echo "     - '{$url['text']}' -> domain: " . ($url['resolved_domain'] ?? 'null') . "\n";
}

// Test content hash generation
$testData = [
    'media_id' => 'test123',
    'user_id' => 'user456',
    'taken_at_iso' => '2024-01-01T00:00:00+00:00',
    'image_url' => 'https://example.com/image.jpg',
    'video_url' => null
];
$hash = generateContentHash($testData);
echo "   ✓ Content hash generated: " . substr($hash, 0, 16) . "...\n";

echo "\n";

// Test 4: Environment Check
echo "4. Environment Check...\n";

$requiredEnvVars = ['IG_SESSIONID', 'IG_CSRF', 'IG_DS_USER_ID'];
$missingVars = [];

foreach ($requiredEnvVars as $var) {
    $value = env($var);
    if (empty($value)) {
        $missingVars[] = $var;
    } else {
        echo "   ✓ $var: " . substr($value, 0, 10) . "...\n";
    }
}

if (!empty($missingVars)) {
    echo "   ⚠ Missing environment variables: " . implode(', ', $missingVars) . "\n";
    echo "     Set these for Instagram API access\n";
}

// Optional environment vars
$optionalVars = ['TESSERACT_PATH', 'GOOGLE_VISION_API_KEY'];
foreach ($optionalVars as $var) {
    $value = env($var);
    if (!empty($value)) {
        echo "   ✓ $var: configured\n";
    } else {
        echo "   - $var: not set (optional)\n";
    }
}

echo "\n";

// Test 5: Dependencies Check
echo "5. Dependencies Check...\n";

// Check for Composer dependencies
if (file_exists(__DIR__ . '/vendor/autoload.php')) {
    echo "   ✓ Composer dependencies installed\n";
} else {
    echo "   ✗ Composer dependencies missing - run 'composer install'\n";
}

// Check for optional dependencies
$extensions = ['imagick', 'gd', 'curl'];
foreach ($extensions as $ext) {
    if (extension_loaded($ext)) {
        echo "   ✓ PHP extension '$ext' loaded\n";
    } else {
        echo "   - PHP extension '$ext' not loaded (may limit functionality)\n";
    }
}

// Check for external tools
$tools = [
    'tesseract' => 'tesseract --version 2>/dev/null',
    'ffmpeg' => 'ffmpeg -version 2>/dev/null'
];

foreach ($tools as $tool => $command) {
    $output = shell_exec($command);
    if ($output) {
        echo "   ✓ $tool available\n";
    } else {
        echo "   - $tool not found (optional for enhanced features)\n";
    }
}

echo "\n=== Test Complete ===\n";
echo "You can now run the enhanced extractor with:\n";
echo "php enhanced_media_extractor.php <USER_ID>\n";
echo "php enhanced_media_extractor.php <USER_ID> --type=reel\n";