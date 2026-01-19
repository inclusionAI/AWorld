# AWorld Documentation Design System

## Overview

This design system is inspired by modern documentation sites like Letta, providing a clean, professional, and user-friendly experience for AWorld documentation.

## Design Principles

### 1. **Clarity First**
- Clear hierarchy with well-defined heading levels
- Generous whitespace for better readability
- Consistent spacing throughout

### 2. **Modern & Professional**
- Contemporary color palette with blue and purple accents
- Smooth animations and transitions
- Glassmorphism effects for depth

### 3. **Accessibility**
- High contrast ratios for text
- Focus states for keyboard navigation
- Semantic HTML structure

### 4. **Responsive**
- Mobile-first approach
- Adaptive layouts for all screen sizes
- Touch-friendly interactive elements

## Color Palette

### Primary Colors
- **Primary Blue**: `#3b82f6` - Main brand color, used for links and primary actions
- **Accent Purple**: `#8b5cf6` - Secondary accent for highlights
- **Success Green**: `#10b981` - Success states and positive actions
- **Warning Amber**: `#f59e0b` - Warnings and cautions
- **Error Red**: `#ef4444` - Errors and critical alerts

### Neutral Colors
- **Text Primary**: `#0f172a` - Main body text
- **Text Secondary**: `#475569` - Secondary text
- **Text Tertiary**: `#64748b` - Less important text
- **Border**: `#cbd5e1` - Default borders
- **Surface**: `#f1f5f9` - Card backgrounds

## Typography

### Font Families
- **Body**: Inter - Modern, highly legible sans-serif
- **Code**: JetBrains Mono - Professional monospace font

### Heading Scale
- **H1**: 2.5rem (40px) - Page titles with gradient effect
- **H2**: 1.875rem (30px) - Major sections with border bottom
- **H3**: 1.5rem (24px) - Subsections
- **H4**: 1.25rem (20px) - Minor sections

### Body Text
- **Size**: 1rem (16px)
- **Line Height**: 1.7
- **Color**: Secondary text color for reduced eye strain

## Components

### Navigation
- **Left Sidebar**: Primary navigation with hierarchical structure
- **Right Sidebar**: Table of contents for current page
- **Top Bar**: Site-wide navigation with glassmorphism effect
- **Active States**: Clear visual feedback with gradient backgrounds

### Content Blocks

#### Code Blocks
- Rounded corners with subtle shadows
- Syntax-specific inline code colors
- Copy button on hover
- Responsive overflow handling

#### Admonitions
- Four types: Note, Warning, Danger, Tip, Info
- Color-coded left borders
- Icon support for visual identification
- Collapsible for detailed information

#### Tables
- Gradient header with primary colors
- Alternating row hover effects
- Rounded corners with border
- Responsive scrolling on mobile

#### Images
- Rounded corners with borders
- Drop shadows for depth
- Hover zoom effect
- Caption support

### Interactive Elements

#### Buttons
- **Primary**: Gradient background with shadow
- **Secondary**: Outlined with border
- **States**: Hover lifts with increased shadow
- **Size**: Comfortable padding for touch

#### Links
- Primary color with underline on hover
- Smooth color transitions
- Focus states for accessibility

## Custom Utilities

### Hero Section (`.aw-hero`)
Perfect for landing pages or major feature introductions.

```html
<div class="aw-hero">
  <h1>Welcome to AWorld</h1>
  <p>Build intelligent agents with ease</p>
  <a class="md-button md-button--primary">Get Started</a>
</div>
```

### Feature Grid (`.aw-feature-grid`)
Responsive grid for showcasing features.

```html
<div class="aw-feature-grid">
  <div class="aw-feature-card">
    <h3>🤖 Build Agents</h3>
    <p>Create intelligent agents declaratively</p>
  </div>
  <!-- More cards... -->
</div>
```

### Badge/Chip (`.aw-chip`, `.aw-badge`)
Inline labels for tags, versions, or status.

```html
<span class="aw-chip">New Feature</span>
<span class="aw-badge">v1.0.0</span>
```

## Spacing System

Consistent spacing using CSS variables:
- **XS**: 0.25rem (4px)
- **SM**: 0.5rem (8px)
- **MD**: 1rem (16px)
- **LG**: 1.5rem (24px)
- **XL**: 2rem (32px)

## Border Radius

Soft, modern corners:
- **SM**: 0.375rem - Small elements
- **Default**: 0.5rem - Buttons, badges
- **MD**: 0.75rem - Cards, inputs
- **LG**: 1rem - Large cards
- **XL**: 1.5rem - Hero sections

## Shadows

Layered depth system:
- **SM**: Subtle - For hover states
- **Default**: Normal - For cards
- **MD**: Medium - For elevated elements
- **LG**: Large - For modals, popovers
- **XL**: Extra Large - For major overlays

## Dark Mode Support

Full dark mode implementation with:
- Adjusted color palette for dark backgrounds
- Reduced shadow intensity
- Maintained contrast ratios
- Smooth theme transitions

Toggle between themes using the theme switcher in the header.

## Responsive Breakpoints

- **Mobile**: < 60em (960px)
- **Tablet**: 60em - 76.1875em (960px - 1219px)
- **Desktop**: > 76.1875em (1219px)

## Accessibility Features

1. **Keyboard Navigation**: Full keyboard support with visible focus states
2. **Screen Readers**: Semantic HTML and ARIA labels
3. **Color Contrast**: WCAG AA compliant
4. **Skip Links**: Quick navigation to main content
5. **Print Styles**: Optimized for printing documentation

## Best Practices

### Content Writing
- Use clear, concise headings
- Break content into scannable sections
- Include code examples with proper syntax highlighting
- Add admonitions for important notes

### Code Examples
- Always specify the language for syntax highlighting
- Keep examples short and focused
- Include comments for complex logic
- Test all code snippets

### Images
- Use descriptive alt text
- Optimize image sizes for web
- Include captions when necessary
- Maintain consistent aspect ratios

## Migration Guide

To apply this design system to existing documentation:

1. Ensure CSS file is linked in your documentation config
2. Review heading hierarchy (only one H1 per page)
3. Convert custom callouts to admonitions
4. Update code blocks with language specifications
5. Add appropriate classes to custom components
6. Test in both light and dark modes

## Further Customization

All design tokens are defined as CSS variables in `:root`, making it easy to customize:

```css
:root {
  --aw-primary: #3b82f6;  /* Change primary color */
  --aw-accent: #8b5cf6;   /* Change accent color */
  /* ... more variables */
}
```

---

**Last Updated**: 2026-01-12
**Version**: 2.0.0
**Design Inspired By**: Letta Documentation
