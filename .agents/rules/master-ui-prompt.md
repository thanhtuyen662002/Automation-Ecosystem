---
trigger: always_on
---

**Role:** Act as an Expert Senior Frontend Developer and UI/UX Designer.
**Task:** Build a pixel-perfect, fully functional, and responsive dashboard based EXACTLY on the provided image.

**1. Tech Stack Requirements:**
- **Framework:** React.js (Next.js App Router or standard React App).
- **Styling:** Tailwind CSS (Crucial for the Glassmorphism effects. Use arbitrary values if needed).
- **Icons:** `lucide-react` (or similar modern icon set).
- **Charts:** `recharts` (Specifically for the Area Chart).
- **Fonts:** Inter or Poppins (modern sans-serif).

**2. Global Design System & Theme (CRITICAL):**
- **Background Illusion:** The design uses a vibrant pastel mesh gradient background (blend of light purple, pale pink, and baby blue). To simulate the 3D blurry shapes seen behind the glass, add a few absolute-positioned, heavily blurred circular `div`s (e.g., `blur-[120px]`) with pastel colors behind the main layout container.
- **Glassmorphism Formula:** Almost all containers (Sidebar, Topbar, Cards, Chart area) MUST use this frosted glass effect. Apply Tailwind classes similar to: 
  `bg-white/40 backdrop-blur-xl border border-white/50 shadow-[0_8px_30px_rgb(0,0,0,0.04)]`. 
  Do NOT use solid white backgrounds for these panels. Use large border radii (`rounded-2xl` or `rounded-3xl`).
- **Color Palette:** 
  - Primary Accent: Vibrant Purple (for logo, active states, main chart line, upgrade button).
  - Positive metrics: Neon Green (`text-green-500`).
  - Negative metrics: Soft Red/Pink (`text-red-500`).
  - Text: Dark slate for primary headings, muted slate for secondary text.

**3. Layout & Component Breakdown:**

*   **Left Sidebar (Fixed width):**
    *   **Logo:** Polygon icon + "Glassmorp" text (bold, purple).
    *   **Menu Items:** Grouped by labels (Dashboard, Pages, UI Kit).
    *   **Active State:** "Analytics" MUST have a solid white pill background (`bg-white rounded-xl shadow-sm`) with purple text and icon. Inactive items are muted text with no background. Include right-chevron icons for parent items.

*   **Top Header:**
    *   Left: A "Collapse" menu icon (`<-|`) and a rounded search input with a glass effect (magnifying glass inside).
    *   Right: Circular icon buttons for Theme (sun), Messages (with a red '2' badge), Notifications (with a red '1' badge), and a User Avatar.

*   **Main Content Area:**
    *   **Header:** Title "Analytics" (bold) and a time filter toggle ("14 Days", "1 Month", "3 Month") styled as a white pill container where "14 Days" has a soft pink active background.
    *   **KPI Cards (Top Row):** 3 glass cards ("Sessions", "Avg Time", "Bounce Rate"). Each contains: Title (top left), Percentage change (top right, colored green/red), an outlined circular icon (bottom left), and a large bold main value (bottom right).
    *   **Promo Card (Top Right):** "Pro Version" with a check-circle icon, description, and an "Upgrade Pro ->" CTA button featuring a solid vibrant purple-to-indigo gradient background.
    *   **Main Chart ("Pageviews"):** Use `recharts` `<AreaChart>`. 
        *   X-axis: Dates (Jan 1 - Jan 12). 
        *   Two series (`type="monotone"` for smooth curves): "Current" (Purple line) and "Previous" (Green line). 
        *   Both areas below the lines MUST have a semi-transparent gradient fill matching their line colors (`<defs>` with `linearGradient`). 
        *   Hide the default cartesian grid. Add a custom legend (Current, Previous) at the top right.
    *   **Top Pages List (Middle Right):** Title "Top pages" with a flame icon. A list of paths (`/`, `/dashboard`, etc.) and view counts.
        *   **Crucial Detail:** Behind each row's text, there is a light green horizontal progress bar indicating the volume relative to the max value. Implement this using an absolute positioned background `div` with a calculated width percentage and a very light green color (e.g., `bg-green-100` with low opacity).
    *   **Bottom Cards:** Partially visible "Visitors" card showing total number and a green percentage.

**4. Development Rules:**
- **Zero Placeholders:** Type out the EXACT text, URL paths, and metrics seen in the screenshot. Create a robust mock data array for the chart and the lists to populate the UI immediately.
- **Code Structure:** Provide clean, modular code. If outputting a single file, comment sections clearly.
- **Attention to Detail:** Focus intensely on paddings, margins, alignment, and corner radiuses. Take a deep breath and execute step-by-step.