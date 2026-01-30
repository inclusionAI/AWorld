#!/usr/bin/env node
/**
 * html2pptx-cli - Command-line tool to convert HTML slides to PowerPoint
 * 
 * USAGE:
 *   node html2pptx-cli.js <output.pptx> <slide1.html> [slide2.html ...] [options]
 * 
 * OPTIONS:
 *   --width <inches>     Slide width in inches (default: auto-detect from HTML)
 *   --height <inches>    Slide height in inches (default: auto-detect from HTML)
 *   --layout <name>      Use preset layout: 16x9, 4x3, 16x10 (overrides width/height)
 *   --title <text>       Presentation title
 *   --author <text>      Presentation author
 *   --tmp <dir>          Temporary directory for gradient images
 *   --skip-validation    Skip strict validation (allows overflow/warnings)
 * 
 * EXAMPLES:
 *   # Basic conversion with auto-detected dimensions
 *   node html2pptx-cli.js output.pptx slide1.html slide2.html slide3.html
 * 
 *   # Use standard 16:9 layout
 *   node html2pptx-cli.js output.pptx slides/*.html --layout 16x9
 * 
 *   # Custom dimensions
 *   node html2pptx-cli.js output.pptx slide.html --width 10 --height 7.5
 * 
 *   # With metadata
 *   node html2pptx-cli.js output.pptx slides/*.html --title "My Presentation" --author "John Doe"
 * 
 * FEATURES:
 *   - Converts HTML slides to PowerPoint with accurate positioning
 *   - Supports CSS gradients (automatically rendered as background images)
 *   - Preserves images, text styling, and layout
 *   - Extracts placeholder positions for charts/tables
 *   - Auto-detects slide dimensions from first HTML file
 */

const html2pptx = require('./html2pptx.js');
const PptxGenJS = require('pptxgenjs');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

// Preset layouts (width x height in inches)
const LAYOUTS = {
    '16x9': { width: 10, height: 5.625 },      // Standard 16:9
    '4x3': { width: 10, height: 7.5 },         // Standard 4:3
    '16x10': { width: 10, height: 6.25 },      // Widescreen 16:10
    'a4': { width: 11.69, height: 8.27 },      // A4 Landscape
    'letter': { width: 11, height: 8.5 }       // US Letter Landscape
};

// Parse command line arguments
function parseArgs(args) {
    const result = {
        output: null,
        slides: [],
        width: null,
        height: null,
        layout: null,
        title: 'Untitled Presentation',
        author: 'html2pptx-cli',
        tmp: null,
        skipValidation: false
    };

    let i = 0;
    while (i < args.length) {
        const arg = args[i];
        
        if (arg === '--width' && args[i + 1]) {
            result.width = parseFloat(args[++i]);
        } else if (arg === '--height' && args[i + 1]) {
            result.height = parseFloat(args[++i]);
        } else if (arg === '--layout' && args[i + 1]) {
            result.layout = args[++i].toLowerCase();
        } else if (arg === '--title' && args[i + 1]) {
            result.title = args[++i];
        } else if (arg === '--author' && args[i + 1]) {
            result.author = args[++i];
        } else if (arg === '--tmp' && args[i + 1]) {
            result.tmp = args[++i];
        } else if (arg === '--skip-validation') {
            result.skipValidation = true;
        } else if (arg === '--help' || arg === '-h') {
            printUsage();
            process.exit(0);
        } else if (!arg.startsWith('-')) {
            // Positional argument
            if (!result.output) {
                result.output = arg;
            } else {
                result.slides.push(arg);
            }
        }
        i++;
    }

    return result;
}

function printUsage() {
    console.log(`
Usage: node html2pptx-cli.js <output.pptx> <slide1.html> [slide2.html ...] [options]

Options:
  --width <inches>     Slide width in inches (default: auto-detect from HTML)
  --height <inches>    Slide height in inches (default: auto-detect from HTML)
  --layout <name>      Use preset layout: 16x9, 4x3, 16x10, a4, letter
  --title <text>       Presentation title
  --author <text>      Presentation author
  --tmp <dir>          Temporary directory for gradient images
  --skip-validation    Skip strict validation (allows overflow/warnings)
  -h, --help           Show this help message

Examples:
  node html2pptx-cli.js output.pptx slide1.html slide2.html
  node html2pptx-cli.js output.pptx slides/*.html --layout 16x9
  node html2pptx-cli.js output.pptx slide.html --width 10 --height 5.625
`);
}

// Auto-detect dimensions from HTML file
async function detectDimensions(htmlFile) {
    const launchOptions = {};
    if (process.platform === 'darwin') {
        launchOptions.channel = 'chrome';
    }
    
    const browser = await chromium.launch(launchOptions);
    try {
        const page = await browser.newPage();
        const filePath = path.isAbsolute(htmlFile) ? htmlFile : path.join(process.cwd(), htmlFile);
        await page.goto(`file://${filePath}`);
        
        const dimensions = await page.evaluate(() => {
            const body = document.body;
            const style = window.getComputedStyle(body);
            return {
                width: parseFloat(style.width),
                height: parseFloat(style.height)
            };
        });
        
        // Convert px to inches (96 DPI)
        return {
            width: dimensions.width / 96,
            height: dimensions.height / 96
        };
    } finally {
        await browser.close();
    }
}

async function main() {
    const args = parseArgs(process.argv.slice(2));
    
    // Validate arguments
    if (!args.output) {
        console.error('Error: Output file is required');
        printUsage();
        process.exit(1);
    }
    
    if (args.slides.length === 0) {
        console.error('Error: At least one HTML slide file is required');
        printUsage();
        process.exit(1);
    }
    
    // Ensure output has .pptx extension
    if (!args.output.endsWith('.pptx')) {
        args.output += '.pptx';
    }
    
    // Resolve slide paths and check existence
    const slidePaths = [];
    for (const slide of args.slides) {
        const slidePath = path.isAbsolute(slide) ? slide : path.join(process.cwd(), slide);
        if (!fs.existsSync(slidePath)) {
            console.error(`Error: Slide file not found: ${slide}`);
            process.exit(1);
        }
        slidePaths.push(slidePath);
    }
    
    console.log('🚀 HTML to PowerPoint Converter\n');
    console.log(`📄 Output: ${args.output}`);
    console.log(`📊 Slides: ${slidePaths.length} file(s)`);
    
    // Determine dimensions
    let width, height;
    
    if (args.layout && LAYOUTS[args.layout]) {
        width = LAYOUTS[args.layout].width;
        height = LAYOUTS[args.layout].height;
        console.log(`📐 Layout: ${args.layout} (${width}" × ${height}")`);
    } else if (args.width && args.height) {
        width = args.width;
        height = args.height;
        console.log(`📐 Dimensions: ${width}" × ${height}" (custom)`);
    } else {
        // Auto-detect from first slide
        console.log('📐 Auto-detecting dimensions from first slide...');
        const detected = await detectDimensions(slidePaths[0]);
        width = detected.width;
        height = detected.height;
        console.log(`📐 Detected: ${width.toFixed(2)}" × ${height.toFixed(2)}"`);
    }
    
    // Create presentation
    const pres = new PptxGenJS();
    pres.defineLayout({ name: 'CUSTOM', width, height });
    pres.layout = 'CUSTOM';
    pres.title = args.title;
    pres.author = args.author;
    
    // Setup temp directory
    const tmpDir = args.tmp || path.join(path.dirname(path.resolve(args.output)), 'tmp_html2pptx');
    if (!fs.existsSync(tmpDir)) {
        fs.mkdirSync(tmpDir, { recursive: true });
    }
    
    console.log(`\n🔄 Converting slides...\n`);
    
    let successCount = 0;
    let failCount = 0;
    const allPlaceholders = [];
    const warnings = [];
    
    for (let i = 0; i < slidePaths.length; i++) {
        const slidePath = slidePaths[i];
        const slideNum = i + 1;
        const fileName = path.basename(slidePath);
        
        process.stdout.write(`  [${slideNum}/${slidePaths.length}] ${fileName}... `);
        
        try {
            const result = await html2pptx(slidePath, pres, {
                tmpDir,
                skipValidation: args.skipValidation
            });
            
            if (result.placeholders && result.placeholders.length > 0) {
                allPlaceholders.push({
                    slide: slideNum,
                    file: fileName,
                    placeholders: result.placeholders
                });
            }
            
            console.log('✅');
            successCount++;
        } catch (error) {
            console.log('❌');
            const errorMsg = error.message;
            console.error(`     Error: ${errorMsg}`);
            
            // Suggest using --skip-validation if validation errors occur
            if (errorMsg.includes('validation') || errorMsg.includes('overflow') || 
                errorMsg.includes('too close') || errorMsg.includes('not supported')) {
                warnings.push({
                    slide: slideNum,
                    file: fileName,
                    suggestion: 'Consider using --skip-validation to convert anyway'
                });
            }
            
            failCount++;
        }
    }
    
    // Save presentation
    const outputPath = path.isAbsolute(args.output) ? args.output : path.join(process.cwd(), args.output);
    
    console.log(`\n💾 Saving: ${outputPath}`);
    
    try {
        await pres.writeFile({ fileName: outputPath });
        
        const stats = fs.statSync(outputPath);
        const sizeKB = (stats.size / 1024).toFixed(2);
        const sizeMB = (stats.size / (1024 * 1024)).toFixed(2);
        
        console.log('\n' + '═'.repeat(50));
        console.log('🎉 Conversion Complete!');
        console.log('═'.repeat(50));
        console.log(`📁 Output:  ${outputPath}`);
        console.log(`📊 Slides:  ${successCount} successful, ${failCount} failed`);
        console.log(`💾 Size:    ${sizeKB} KB (${sizeMB} MB)`);
        console.log(`📐 Layout:  ${width.toFixed(2)}" × ${height.toFixed(2)}"`);
        
        // Report placeholders if any
        if (allPlaceholders.length > 0) {
            console.log('\n📍 Placeholders detected:');
            for (const slideInfo of allPlaceholders) {
                for (const p of slideInfo.placeholders) {
                    console.log(`   Slide ${slideInfo.slide}: ${p.id} (${p.w.toFixed(2)}" × ${p.h.toFixed(2)}")`);
                }
            }
            console.log('\n   Use PptxGenJS to add charts/tables to these areas.');
        }
        
        // Show suggestions for failed slides
        if (warnings.length > 0 && !args.skipValidation) {
            console.log('\n💡 Tip: Some slides failed validation. Use --skip-validation to convert anyway.');
        }
        
        console.log('═'.repeat(50));
        
    } catch (error) {
        console.error(`\n❌ Failed to save: ${error.message}`);
        process.exit(1);
    }
}

// Run
main().catch(error => {
    console.error(`\n💥 Unexpected error: ${error.message}`);
    process.exit(1);
});
