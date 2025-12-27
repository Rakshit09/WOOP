// WOOP Application JavaScript
// Expects window.WOOP_CONFIG to be defined with { projects: [...], directReports: [...] }

// State & Configuration
const projectList = window.WOOP_CONFIG?.projects || [];
const directReports = window.WOOP_CONFIG?.directReports || [];

let rowCounter = 0;
let activeDropdown = null;
let highlightedIndex = -1;
let activityMapData = null;
let currentEntryType = 'forecast';
let currentDate = null;
let dropdownDebounceTimer = null;

// Nudge state
let pendingNudges = [];
let currentNudgeIndex = 0;
const nudgeArts = ['👀', '🫵', '😤', '🧚', '🔔', '📢', '🚨', '💪'];

// Week dropdown items
let weekDropdownItems = [];

// Project Breakdown Donut Chart Colors
const barGradients = [
    { base: '#e3f2fd', gradient: '#e3f2fd' },
    { base: '#bbdefb', gradient: '#bbdefb' },
    { base: '#90caf9', gradient: '#90caf9' },
    { base: '#64b5f6', gradient: '#64b5f6' },
    { base: '#42a5f5', gradient: '#42a5f5' },
    { base: '#2196f3', gradient: '#2196f3' },
    { base: '#1e88e5', gradient: '#1e88e5' },
    { base: '#1976d2', gradient: '#1976d2' },
    { base: '#1565c0', gradient: '#1565c0' },
];

// Utility: Debounce function for performance
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

// Logo fallback for Posit Connect path handling
function tryAlternativeLogoPath(img) {
    const paths = [
        './static/logo.png',
        '../static/logo.png', 
        'static/logo.png',
        '/static/logo.png'
    ];
    
    let currentIndex = 0;
    
    function tryNext() {
        if (currentIndex < paths.length) {
            console.log('Trying logo path:', paths[currentIndex]);
            img.src = paths[currentIndex];
            currentIndex++;
        } else {
            console.warn('All logo paths failed, hiding logo');
            img.style.display = 'none';
        }
    }
    
    img.onerror = tryNext;
    tryNext();
}

// Activity Map Functions
async function loadActivityMap() {
    try {
        console.log('Loading activity map...');
        const response = await fetch('api/activity_map');
        console.log('Activity map response status:', response.status);
        
        if (!response.ok) {
            const errorText = await response.text();
            console.error('Activity map error:', errorText);
            throw new Error('Failed to load activity map: ' + errorText);
        }
        
        activityMapData = await response.json();
        console.log('Activity map loaded:', activityMapData.forecasts?.length, 'forecasts,', activityMapData.actuals?.length, 'actuals');
        requestAnimationFrame(() => renderActivityMap());
    } catch (error) {
        console.error('Error loading activity map:', error);
        // Still hide loader on error
        const loader = document.getElementById('myActivityLoader');
        const content = document.getElementById('myActivityContent');
        if (loader) loader.style.display = 'none';
        if (content) content.style.display = 'flex';
    }
}

function highlightActivityCell(date, type) {
    if (!activityMapData) return;

    // 1. Remove highlight from ALL cells (both rows)
    document.querySelectorAll('.activity-cell.highlighted').forEach(cell => {
        cell.classList.remove('highlighted');
    });
    
    // 2. Identify the correct row
    const rowId = type === 'forecast' ? 'forecastRow' : 'actualRow';
    const row = document.getElementById(rowId);
    
    if (row) {
        // 3. Find the index of the date in the data
        const dataArray = type === 'forecast' ? activityMapData.forecasts : activityMapData.actuals;
        const index = dataArray.findIndex(item => item.date === date);
        
        // 4. Find the corresponding DOM element by index
        if (index !== -1) {
            const cells = row.querySelectorAll('.activity-cell');
            if (cells[index]) {
                const cell = cells[index];
                
                // Add the class to trigger CSS animation
                cell.classList.add('highlighted');
                
                // Scroll the scrollable container so the cell is visible
                cell.scrollIntoView({ 
                    behavior: 'smooth', 
                    block: 'nearest', 
                    inline: 'center' // Keeps the cell centered horizontally
                });
            }
        }
    }
}

function renderActivityMap() {
    if (!activityMapData) return;
    
    const forecastRow = document.getElementById('forecastRow');
    const actualRow = document.getElementById('actualRow');
    const monthLabelsRow = document.getElementById('monthLabelsRow');
    
    const monthNames = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    const cellSize = 15;
    const gapSize = 2;
    
    // Calculate month spans
    let monthSpans = [];
    let currentMonth = -1;
    let currentMonthStart = 0;
    
    activityMapData.forecasts.forEach((item, index) => {
        const date = new Date(item.date + 'T00:00:00');
        const month = date.getMonth();
        
        if (month !== currentMonth) {
            if (currentMonth !== -1) {
                monthSpans.push({
                    month: currentMonth,
                    startIndex: currentMonthStart,
                    endIndex: index - 1
                });
            }
            currentMonth = month;
            currentMonthStart = index;
        }
    });
    
    if (currentMonth !== -1) {
        monthSpans.push({
            month: currentMonth,
            startIndex: currentMonthStart,
            endIndex: activityMapData.forecasts.length - 1
        });
    }
    
    // Build HTML strings efficiently
    const monthLabelsHTML = monthSpans.map(span => {
        const count = span.endIndex - span.startIndex + 1;
        const width = (count * cellSize) + ((count - 1) * gapSize);
        return `<span class="month-label" style="width:${width}px">${monthNames[span.month]}</span>`;
    }).join('');
    
    // Custom tooltips with styled spans
    const forecastHTML = activityMapData.forecasts.map(item => 
        `<div class="activity-cell cell-${item.status}" onclick="handleCellClick('${item.date}','forecast',${item.has_entry},'${item.status}')"><span class="tooltip">${formatDateLabel(item.date)} - ${getStatusLabel(item.status,'forecast')}</span></div>`
    ).join('');
    
    const actualHTML = activityMapData.actuals.map(item => 
        `<div class="activity-cell cell-${item.status}" onclick="handleCellClick('${item.date}','actual',${item.has_entry},'${item.status}')"><span class="tooltip">${formatDateLabel(item.date)} - ${getStatusLabel(item.status,'actual')}</span></div>`
    ).join('');
    
    // Batch DOM updates
    monthLabelsRow.innerHTML = monthLabelsHTML;
    forecastRow.innerHTML = forecastHTML;
    actualRow.innerHTML = actualHTML;
    
    // Calculate % filled (actuals: green / (green + red))
    let greenCount = 0, redCount = 0;
    activityMapData.actuals.forEach(a => {
        if (a.status === 'green') greenCount++;
        else if (a.status === 'red') redCount++;
    });
    const totalApplicable = greenCount + redCount;
    const fillPercent = totalApplicable > 0 ? Math.round((greenCount / totalApplicable) * 100) : 100;
    
    // Update donut center with % filled
    const donutCenterValue = document.getElementById('donutCenterValue');
    if (donutCenterValue) {
        donutCenterValue.textContent = `${fillPercent}%`;
        donutCenterValue.style.color = fillPercent >= 90 ? '#10b981' : fillPercent >= 70 ? '#f97316' : '#ef4444';
    }
    
    // Hide loader, show content
    const loader = document.getElementById('myActivityLoader');
    const content = document.getElementById('myActivityContent');
    if (loader) loader.style.display = 'none';
    if (content) content.style.display = 'flex';
    if (currentDate && currentEntryType) {
        highlightActivityCell(currentDate, currentEntryType);
    }
}

function getStatusLabel(status, type) {
    const labels = {
        green: 'Completed',
        red: 'Missing Actuals',
        blue: 'Open for Input',
        gray: type === 'forecast' ? 'Expired' : 'Locked'
    };
    return labels[status] || status;
}

// Position tooltip above the donut (no scrolling)
function positionTooltip(tooltip, angle) {
    tooltip.style.bottom = 'calc(100% + 8px)';
    tooltip.style.left = '50%';
    tooltip.style.transform = 'translateX(-50%)';
    tooltip.style.top = 'auto';
    tooltip.style.right = 'auto';
}

async function loadMyProjectBreakdown() {
    try {
        const response = await fetch('api/project_breakdown');
        if (!response.ok) return;
        
        const data = await response.json();
        renderDonutChart(data.breakdown);
    } catch (error) {
        console.error('Error loading project breakdown:', error);
    }
}

function renderDonutChart(breakdown) {
    const svg = document.getElementById('myDonutChart');
    const tooltip = document.getElementById('donutTooltip');
    
    if (!svg || !breakdown || breakdown.length === 0) {
        return;
    }
    
    // SVG donut parameters
    const cx = 50, cy = 50, r = 42;
    const circumference = 2 * Math.PI * r;
    
    // Clear previous segments (keep the empty circle as fallback)
    const existingSegments = svg.querySelectorAll('.donut-segment');
    existingSegments.forEach(seg => seg.remove());
    
    // Build donut segments
    let cumulativePercent = 0;
    breakdown.forEach((item, index) => {
        const color = barGradients[index % barGradients.length].base;
        const percent = item.percentage / 100;
        const dashLength = percent * circumference;
        const dashOffset = -cumulativePercent * circumference;
        
        // Calculate segment midpoint angle (for tooltip positioning)
        const startAngle = cumulativePercent * 360 - 90;
        const midAngle = startAngle + (percent * 360 / 2);
        
        const circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
        circle.classList.add('donut-segment');
        circle.setAttribute('cx', cx);
        circle.setAttribute('cy', cy);
        circle.setAttribute('r', r);
        circle.setAttribute('stroke', color);
        circle.setAttribute('stroke-dasharray', `${dashLength} ${circumference - dashLength}`);
        circle.setAttribute('stroke-dashoffset', dashOffset);
        circle.style.animationDelay = `${index * 0.1}s`;
        
        // Tooltip events with dynamic positioning
        circle.addEventListener('mouseenter', () => {
            tooltip.innerHTML = `<strong>${item.project}</strong><br><span style="display: block; text-align: center;">${item.percentage}%</span>`;
            positionTooltip(tooltip, midAngle);
            tooltip.classList.add('show');
        });
        circle.addEventListener('mouseleave', () => {
            tooltip.classList.remove('show');
        });
        
        svg.appendChild(circle);
        cumulativePercent += percent;
    });
    
    // Hide empty circle if we have data
    const emptyCircle = svg.querySelector('.donut-empty');
    if (emptyCircle) emptyCircle.style.display = breakdown.length > 0 ? 'none' : 'block';
}

async function loadTeamMemberProjectBreakdown(email, memberContainer) {
    try {
        const response = await fetch(`api/project_breakdown?email=${encodeURIComponent(email)}`);
        if (!response.ok) return;
        
        const data = await response.json();
        const svg = memberContainer.querySelector('.team-donut-chart');
        const tooltip = memberContainer.querySelector('.team-donut-tooltip');
        
        if (svg) {
            renderTeamDonutChart(svg, tooltip, data.breakdown);
        }
    } catch (error) {
        console.error(`Error loading project breakdown for ${email}:`, error);
    }
}

function renderTeamDonutChart(svg, tooltip, breakdown) {
    if (!svg || !breakdown || breakdown.length === 0) {
        return;
    }
    
    // SVG donut parameters
    const cx = 50, cy = 50, r = 42;
    const circumference = 2 * Math.PI * r;
    
    // Clear previous segments (keep the empty circle as fallback)
    const existingSegments = svg.querySelectorAll('.donut-segment');
    existingSegments.forEach(seg => seg.remove());
    
    // Build donut segments
    let cumulativePercent = 0;
    breakdown.forEach((item, index) => {
        const color = barGradients[index % barGradients.length].base;
        const percent = item.percentage / 100;
        const dashLength = percent * circumference;
        const dashOffset = -cumulativePercent * circumference;
        
        // Calculate segment midpoint angle (for tooltip positioning)
        const startAngle = cumulativePercent * 360 - 90;
        const midAngle = startAngle + (percent * 360 / 2);
        
        const circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
        circle.classList.add('donut-segment');
        circle.setAttribute('cx', cx);
        circle.setAttribute('cy', cy);
        circle.setAttribute('r', r);
        circle.setAttribute('stroke', color);
        circle.setAttribute('stroke-dasharray', `${dashLength} ${circumference - dashLength}`);
        circle.setAttribute('stroke-dashoffset', dashOffset);
        circle.style.animationDelay = `${index * 0.1}s`;
        
        // Tooltip events with dynamic positioning
        if (tooltip) {
            circle.addEventListener('mouseenter', () => {
                tooltip.innerHTML = `<strong>${item.project}</strong><br><span style="display: block; text-align: center;">${item.percentage}%</span>`;
                positionTooltip(tooltip, midAngle);
                tooltip.classList.add('show');
            });
            circle.addEventListener('mouseleave', () => {
                tooltip.classList.remove('show');
            });
        }
        
        svg.appendChild(circle);
        cumulativePercent += percent;
    });
    
    // Hide empty circle if we have data
    const emptyCircle = svg.querySelector('.donut-empty');
    if (emptyCircle) emptyCircle.style.display = breakdown.length > 0 ? 'none' : 'block';
}

function formatDateLabel(dateStr) {
    const date = new Date(dateStr + 'T00:00:00');
    return date.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' });
}

// Week Selection Dropdown
async function loadOutstandingItems() {
    try {
        console.log('Loading outstanding items...');
        const response = await fetch('api/outstanding_items');
        console.log('Outstanding items response status:', response.status);
        
        if (!response.ok) {
            const errorText = await response.text();
            console.error('Outstanding items error:', errorText);
            throw new Error('Failed to load outstanding items: ' + errorText);
        }
        
        const items = await response.json();
        console.log('Outstanding items loaded:', items.length, 'items');
        weekDropdownItems = items;
        
        const dropdown = document.getElementById('weekDropdown');
        dropdown.innerHTML = '';
        
        // Separate items into outstanding and forecast
        const outstandingItems = items.filter(item => item.type === 'actual');
        const forecastItems = items.filter(item => item.type === 'forecast');
        
        // Add Outstanding group if there are outstanding items
        if (outstandingItems.length > 0) {
            const group = document.createElement('div');
            group.className = 'week-dropdown-group';
            group.innerHTML = `<div class="week-dropdown-label outstanding">⚠️ Outstanding</div>`;
            
            outstandingItems.forEach(item => {
                const itemEl = document.createElement('div');
                itemEl.className = 'week-dropdown-item outstanding-item';
                itemEl.dataset.value = JSON.stringify({ date: item.date, type: item.type, status: item.status });
                itemEl.innerHTML = `
                    <svg class="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path>
                    </svg>
                    <span>${item.week_commencing_label}</span>
                `;
                itemEl.onclick = () => selectWeekItem(item, itemEl);
                group.appendChild(itemEl);
            });
            
            dropdown.appendChild(group);
        }
        
        // Add Forecast group
        if (forecastItems.length > 0) {
            const group = document.createElement('div');
            group.className = 'week-dropdown-group';
            group.innerHTML = `<div class="week-dropdown-label forecast">📅 Forecast</div>`;
            
            forecastItems.forEach(item => {
                const itemEl = document.createElement('div');
                itemEl.className = 'week-dropdown-item forecast-item';
                itemEl.dataset.value = JSON.stringify({ date: item.date, type: item.type, status: item.status });
                itemEl.innerHTML = `
                    <svg class="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"></path>
                    </svg>
                    <span>${item.week_commencing_label}</span>
                `;
                itemEl.onclick = () => selectWeekItem(item, itemEl);
                group.appendChild(itemEl);
            });
            
            dropdown.appendChild(group);
        }
        
        // auto-select first available forecast
        if (forecastItems.length > 0 && !currentDate) {
            const firstForecast = forecastItems[0];
            currentDate = firstForecast.date;
            currentEntryType = 'forecast';
            
            // update trigger text
            const trigger = document.getElementById('weekSelectTrigger');
            trigger.textContent = firstForecast.week_commencing_label;
            trigger.classList.remove('placeholder');
            
            // update hidden value
            document.getElementById('weekDateValue').value = JSON.stringify({ date: firstForecast.date, type: 'forecast', status: firstForecast.status });
            
            // mark item as selected
            const firstItem = dropdown.querySelector('.forecast-item');
            if (firstItem) firstItem.classList.add('selected');
            
            updateEntryBadge('forecast');
            highlightActivityCell(firstForecast.date, 'forecast');
        }
        
    } catch (error) {
        console.error('Error loading outstanding items:', error);
        const dropdown = document.getElementById('weekDropdown');
        const trigger = document.getElementById('weekSelectTrigger');
        if (dropdown) {
            dropdown.innerHTML = `<div class="week-dropdown-group">
                <div class="week-dropdown-label" style="color: #dc2626;">⚠️ Error Loading Weeks</div>
                <div class="week-dropdown-item" style="color: #6b7280; cursor: default; font-size: 12px;">
                    ${error.message || 'Unable to load weeks. Check console for details.'}
                </div>
            </div>`;
        }
        if (trigger) {
            trigger.textContent = 'Error loading weeks';
            trigger.classList.add('placeholder');
        }
    }
}

function toggleWeekDropdown() {
    const dropdown = document.getElementById('weekDropdown');
    const trigger = document.getElementById('weekSelectTrigger');
    const isOpen = dropdown.classList.contains('open');
    
    if (isOpen) {
        closeWeekDropdown();
    } else {
        dropdown.classList.add('open');
        trigger.classList.add('open');
    }
}

function closeWeekDropdown() {
    const dropdown = document.getElementById('weekDropdown');
    const trigger = document.getElementById('weekSelectTrigger');
    dropdown.classList.remove('open');
    trigger.classList.remove('open');
}

function selectWeekItem(item, itemEl) {
    const trigger = document.getElementById('weekSelectTrigger');
    const dropdown = document.getElementById('weekDropdown');
    
    // update trigger text
    trigger.textContent = item.week_commencing_label;
    trigger.classList.remove('placeholder');
    
    // update hidden value
    document.getElementById('weekDateValue').value = JSON.stringify({ date: item.date, type: item.type, status: item.status });
    
    // update selected state
    dropdown.querySelectorAll('.week-dropdown-item').forEach(el => el.classList.remove('selected'));
    itemEl.classList.add('selected');
    
    // close dropdown
    closeWeekDropdown();
    
    // trigger selection
    selectDate(item.date, item.type, item.status === 'green');
}

// Date selection (edit guard on)
function handleCellClick(date, type, hasEntry, status) {
    if (status === 'gray') {
        showToast(type === 'forecast' ? 'This forecast has expired' : 'Future actuals are locked', 'error');
        return;
    }
    
    selectDate(date, type, hasEntry);
}

async function selectDate(date, type, hasEntry = false) {
    console.log('selectDate called:', { date, type, hasEntry });
    
    try {
        // if entry exists - Green
        if (hasEntry) {
            const confirmed = confirm('This week was already submitted. Are you sure you want to modify it?');
            if (!confirmed) return;
        }
        
        // Update state
        currentDate = date;
        currentEntryType = type;
        document.getElementById('entryType').value = type;
        highlightActivityCell(date, type);
        
        // sync dropdown selection
        const dropdown = document.getElementById('weekDropdown');
        const trigger = document.getElementById('weekSelectTrigger');
        const items = dropdown.querySelectorAll('.week-dropdown-item');
        
        let foundInDropdown = false;
        
        // clear existing selections
        dropdown.querySelectorAll('.week-dropdown-item').forEach(el => el.classList.remove('selected'));
        
        items.forEach(item => {
            try {
                const itemData = JSON.parse(item.dataset.value);
                if (itemData.date === date && itemData.type === type) {
                    foundInDropdown = true;
                    
                    // mark selected
                    item.classList.add('selected');
                    
                    // trigger text
                    const labelSpan = item.querySelector('span');
                    if (labelSpan) {
                        trigger.textContent = labelSpan.textContent;
                        trigger.classList.remove('placeholder');
                    }
                    
                    // hidden value
                    document.getElementById('weekDateValue').value = item.dataset.value;
                }
            } catch (e) {
                console.error('Error parsing dropdown item:', e);
            }
        });
        
        // when date not in dropdown, update the trigger to show the selected date
        if (!foundInDropdown) {
            console.log('Date not found in dropdown, formatting manually');
            
            // Calculate Start Date (Monday)
            const startObj = new Date(date + 'T00:00:00');
            if (type === 'actual') {
                startObj.setDate(startObj.getDate() - 4);
            }
            const endObj = new Date(startObj);
            endObj.setDate(startObj.getDate() + 4); // Add 4 days to Monday to get Friday

            // function to format dates (e.g., "Jan 24, 2025")
            const formatDatePart = (dateObj) => {
                const monthNames = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
                const month = monthNames[dateObj.getMonth()];
                const day = String(dateObj.getDate()).padStart(2, '0');
                const year = dateObj.getFullYear();
                return `${month} ${day}, ${year}`;
            };

            // range string: "Jan 20, 2025 - Jan 24, 2025"
            const formattedDate = `${formatDatePart(startObj)} - ${formatDatePart(endObj)}`;
            
            console.log('Formatted date range:', formattedDate);
            
            trigger.textContent = formattedDate;
            trigger.classList.remove('placeholder');
            
            document.getElementById('weekDateValue').value = JSON.stringify({ 
                date: date, 
                type: type, 
                status: hasEntry ? 'green' : 'blue'
            });
        }
        
        // update badge
        updateEntryBadge(type);
        
        // existing entries load
        console.log('Loading entries for:', date, type);
        await loadEntriesForDate(date, type);
        
        showToast(`Loaded ${type} for ${formatDateLabel(date)}`, 'info');
        
    } catch (error) {
        console.error('Error in selectDate:', error);
        showToast('Error loading date: ' + error.message, 'error');
    }
}

function updateEntryBadge(type) {
    const badge = document.getElementById('entryTypeBadge');
    const label = document.getElementById('entryTypeLabel');
    
    if (type === 'hidden') {
        badge.style.display = 'none';
    } else if (type === 'forecast') {
        badge.style.display = 'inline-flex';
        badge.className = 'entry-badge badge-forecast';
        label.textContent = 'Forecast';
    } else {
        badge.style.display = 'inline-flex';
        badge.className = 'entry-badge badge-actual';
        label.textContent = 'Actual';
    }
}


function renderTeamMemberActivityMap(email, data) {
    const memberContainer = document.querySelector(`.team-member-activity[data-email="${email}"]`);
    if (!memberContainer) return;
    
    const forecastRow = memberContainer.querySelector('.team-forecast-row');
    const actualRow = memberContainer.querySelector('.team-actual-row');
    const monthLabelsRow = memberContainer.querySelector('.team-month-labels');
    
    const monthNames = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    const cellSize = 15;
    const gapSize = 2;
    
    // Calculate month spans
    let monthSpans = [];
    let currentMonth = -1;
    let currentMonthStart = 0;
    
    // Use data.forecasts for timeline structure
    if (data.forecasts) {
        data.forecasts.forEach((item, index) => {
            const date = new Date(item.date + 'T00:00:00');
            const month = date.getMonth();
            
            if (month !== currentMonth) {
                if (currentMonth !== -1) {
                    monthSpans.push({ month: currentMonth, startIndex: currentMonthStart, endIndex: index - 1 });
                }
                currentMonth = month;
                currentMonthStart = index;
            }
        });
        
        if (currentMonth !== -1) {
            monthSpans.push({ month: currentMonth, startIndex: currentMonthStart, endIndex: data.forecasts.length - 1 });
        }
    }
    
    // HTML as strings for batch DOM update
    const monthLabelsHTML = monthSpans.map(span => {
        const count = span.endIndex - span.startIndex + 1;
        const width = (count * cellSize) + ((count - 1) * gapSize);
        return `<span class="month-label" style="width:${width}px">${monthNames[span.month]}</span>`;
    }).join('');
    
    // Tooltips (Read only for team members)
    const forecastHTML = (data.forecasts || []).map(item => 
        `<div class="activity-cell cell-${item.status} team-cell-readonly">
            <span class="tooltip">${formatDateLabel(item.date)} - ${getStatusLabel(item.status,'forecast')}</span>
        </div>`
    ).join('');
    
    const actualHTML = (data.actuals || []).map(item => 
        `<div class="activity-cell cell-${item.status} team-cell-readonly">
            <span class="tooltip">${formatDateLabel(item.date)} - ${getStatusLabel(item.status,'actual')}</span>
        </div>`
    ).join('');
    
    // DOM updates
    if (monthLabelsRow) monthLabelsRow.innerHTML = monthLabelsHTML;
    if (forecastRow) forecastRow.innerHTML = forecastHTML;
    if (actualRow) actualRow.innerHTML = actualHTML;
    
    // Calculate % filled (actuals: green / (green + red))
    let greenCount = 0, redCount = 0;
    (data.actuals || []).forEach(a => {
        if (a.status === 'green') greenCount++;
        else if (a.status === 'red') redCount++;
    });
    const totalApplicable = greenCount + redCount;
    const fillPercent = totalApplicable > 0 ? Math.round((greenCount / totalApplicable) * 100) : 100;
    
    // Update % filled display in donut center
    const fillPercentEl = memberContainer.querySelector('.team-donut-value');
    if (fillPercentEl) {
        fillPercentEl.textContent = `${fillPercent}%`;
        fillPercentEl.style.color = fillPercent >= 90 ? '#10b981' : fillPercent >= 70 ? '#f97316' : '#ef4444';
    }
}

async function loadEntriesForDate(date, type) {
    try {
        const response = await fetch(`api/get_entry?date=${date}&type=${type}`);
        if (!response.ok) throw new Error('Failed to load entries');
        
        const data = await response.json();
        
        // clear rows
        document.getElementById('rowsContainer').innerHTML = '';
        rowCounter = 0;
        
        if (data.entries && data.entries.length > 0) {
            data.entries.forEach(entry => {
                addNewRow(entry.project, entry.days, entry.notes);
            });
        } else {
            addNewRow();
        }
        
    } catch (error) {
        console.error('Error loading entries:', error);
        document.getElementById('rowsContainer').innerHTML = '';
        rowCounter = 0;
        addNewRow();
    }
}

// Toast notifications
function showToast(message, type = 'info') {
    const toast = document.getElementById('toast');
    const messageEl = document.getElementById('toast-message');
    messageEl.textContent = message;
    
    const iconSvg = toast.querySelector('svg');
    if (type === 'success') {
        iconSvg.innerHTML = '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M5 13l4 4L19 7"></path>';
    } else if (type === 'error') {
        iconSvg.innerHTML = '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M6 18L18 6M6 6l12 12"></path>';
    } else {
        iconSvg.innerHTML = '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path>';
    }
    
    toast.className = `toast ${type} show`;
    setTimeout(() => toast.classList.remove('show'), 3500);
}

// Project dropdown functions 
function createDropdown(inputElement, rowId) {
    if (dropdownDebounceTimer) {
        clearTimeout(dropdownDebounceTimer);
    }
    
    dropdownDebounceTimer = setTimeout(() => {
        _createDropdownImmediate(inputElement, rowId, false); 
    }, 50);
}

function createDropdownImmediate(inputElement, rowId) {
    if (dropdownDebounceTimer) {
        clearTimeout(dropdownDebounceTimer);
    }
    _createDropdownImmediate(inputElement, rowId, true); 
}

function _createDropdownImmediate(inputElement, rowId, showAll = false) {
    closeAllDropdowns();
    
    if (!inputElement || !document.body.contains(inputElement)) {
        return;
    }
    
    const wrapper = inputElement.closest('.project-wrapper');
    if (!wrapper) {
        return;
    }
    
    const dropdown = document.createElement('div');
    dropdown.className = 'custom-dropdown';
    dropdown.id = `dropdown-${rowId}`;
    
    const filterValue = showAll ? '' : inputElement.value.toLowerCase().trim();
    const filteredProjects = projectList.filter(p => 
        p.toLowerCase().includes(filterValue)
    );
    
    // Build HTML as string 
    let html = '';
    if (filteredProjects.length === 0) {
        html = `<div class="dropdown-item" style="color:#94a3b8;cursor:default">
            <svg class="w-5 h-5 opacity-50" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.172 16.172a4 4 0 015.656 0M9 10h.01M15 10h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path>
            </svg>
            No matching projects found
        </div>`;
    } else {
        // Limit to first 50 items 
        const displayProjects = filteredProjects.slice(0, 50);
        html = displayProjects.map((project, index) => {
            let displayText = project;
            if (filterValue) {
                const regex = new RegExp(`(${filterValue.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi');
                displayText = project.replace(regex, '<span style="color:#0078D4;font-weight:700">$1</span>');
            }
            return `<div class="dropdown-item" data-index="${index}" data-project="${project.replace(/"/g, '&quot;')}">
                <div class="w-8 h-8 rounded-lg bg-brand-light flex items-center justify-center flex-shrink-0">
                    <svg class="w-4 h-4 text-brand-dark/50" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2"></path>
                    </svg>
                </div>
                <span>${displayText}</span>
            </div>`;
        }).join('');
    }
    
    dropdown.innerHTML = html;
    
    // Event delegation 
    dropdown.addEventListener('click', (e) => {
        const item = e.target.closest('.dropdown-item');
        if (item && item.dataset.project) {
            selectProject(inputElement, item.dataset.project, rowId);
        }
    });
    
    wrapper.appendChild(dropdown);
    
    // Raise z-index above other rows
    const parentRow = inputElement.closest('.timesheet-row');
    if (parentRow) {
        parentRow.classList.add('dropdown-active');
    }
    
    activeDropdown = { element: dropdown, input: inputElement, items: filteredProjects, row: parentRow };
    highlightedIndex = -1;
}

function selectProject(inputElement, project, rowId) {
    inputElement.value = project;
    closeAllDropdowns();
    
    setTimeout(() => {
        const row = document.getElementById(rowId);
        const daysInput = row.querySelector('.days-input');
        daysInput.focus();
        daysInput.select();
    }, 50);
}

function closeAllDropdowns() {
    if (dropdownDebounceTimer) {
        clearTimeout(dropdownDebounceTimer);
        dropdownDebounceTimer = null;
    }
    if (activeDropdown && activeDropdown.row) {
        activeDropdown.row.classList.remove('dropdown-active');
    }
    document.querySelectorAll('.timesheet-row.dropdown-active').forEach(row => {
        row.classList.remove('dropdown-active');
    });
    document.querySelectorAll('.custom-dropdown').forEach(d => d.remove());
    activeDropdown = null;
    highlightedIndex = -1;
}

function handleDropdownKeydown(e, inputElement, rowId) {
    if (!activeDropdown) {
        if (e.key === 'ArrowDown' || (e.key === 'Enter' && !inputElement.value)) {
            createDropdown(inputElement, rowId);
            e.preventDefault();
        }
        return;
    }
    
    const items = activeDropdown.element.querySelectorAll('.dropdown-item:not([style*="cursor: default"])');
    
    switch(e.key) {
        case 'ArrowDown':
            e.preventDefault();
            highlightedIndex = Math.min(highlightedIndex + 1, items.length - 1);
            updateHighlight(items);
            break;
        case 'ArrowUp':
            e.preventDefault();
            highlightedIndex = Math.max(highlightedIndex - 1, 0);
            updateHighlight(items);
            break;
        case 'Enter':
            e.preventDefault();
            if (highlightedIndex >= 0 && items[highlightedIndex]) {
                items[highlightedIndex].click();
            } else if (items.length > 0) {
                items[0].click();
            }
            break;
        case 'Escape':
        case 'Tab':
            closeAllDropdowns();
            break;
    }
}

function updateHighlight(items) {
    items.forEach((item, index) => {
        item.classList.toggle('highlighted', index === highlightedIndex);
        if (index === highlightedIndex) {
            item.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
        }
    });
}

// Row management
function addNewRow(project = '', days = '', notes = '') {
    closeAllDropdowns();
    
    const rowId = `row-${rowCounter++}`;
    const container = document.getElementById('rowsContainer');
    
    // Prevent XSS
    const escapeHtml = (str) => str.replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    
    const html = `<div id="${rowId}" class="timesheet-grid timesheet-row border-b border-gray-100 row-enter">
        <div class="delete-cell bg-brand-dark flex items-center justify-center cursor-pointer" onclick="deleteRow('${rowId}')" title="Delete row">
            <svg class="w-4 h-4 text-white/80" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"></path></svg>
        </div>
        <div class="input-container bg-brand-light/60 border-r border-gray-100 relative project-wrapper">
            <input type="text" class="input-field project-input text-brand-dark font-medium pr-14" value="${escapeHtml(project)}" placeholder="Search or select project..." autocomplete="off" onclick="createDropdownImmediate(this,'${rowId}')" oninput="createDropdown(this,'${rowId}')" onkeydown="handleDropdownKeydown(event,this,'${rowId}')">
            <div class="chevron-btn absolute right-0 top-1 bottom-1 w-10 bg-brand-dark rounded-r-lg flex items-center justify-center cursor-pointer" onclick="event.stopPropagation();this.previousElementSibling.click()">
                <svg class="w-4 h-4 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M19 9l-7 7-7-7"></path></svg>
            </div>
        </div>
        <div class="input-container bg-white border-r border-gray-100 flex items-center justify-center">
            <input type="number" class="input-field days-input text-center text-gray-900 text-lg font-bold" step="0.5" min="0" max="7" value="${days}" placeholder="0.0" oninput="debouncedCalculateTotal()" onkeydown="handleDaysKeydown(event,'${rowId}')">
        </div>
        <div class="input-container bg-white">
            <input type="text" class="input-field notes-input text-gray-600" value="${escapeHtml(notes)}" placeholder="Add notes..." onkeydown="handleNotesKeydown(event,'${rowId}')">
        </div>
    </div>`;

    container.insertAdjacentHTML('beforeend', html);
    calculateTotal();
}

// Debounced calculate total 
const debouncedCalculateTotal = debounce(calculateTotal, 100);

function handleDaysKeydown(e, rowId) {
    if (e.key === 'Enter') {
        e.preventDefault();
        const row = document.getElementById(rowId);
        row.querySelector('.notes-input').focus();
    }
}

function handleNotesKeydown(e, rowId) {
    if (e.key === 'Enter') {
        e.preventDefault();
        addNewRow();
    }
}

function deleteRow(rowId) {
    const row = document.getElementById(rowId);
    if (row) {
        row.classList.add('row-deleting');
        setTimeout(() => {
            row.remove();
            calculateTotal();
            
            const rows = document.querySelectorAll('#rowsContainer > div');
            if (rows.length === 0) addNewRow();
        }, 150);
    }
}

function clearAll() {
    const rows = document.querySelectorAll('#rowsContainer > div');
    if (rows.length === 0) return;
    
    if (confirm('Are you sure you want to clear all entries?')) {
        document.getElementById('rowsContainer').innerHTML = '';
        rowCounter = 0;
        addNewRow();
        calculateTotal();
        showToast('All entries cleared', 'info');
    }
}

function calculateTotal() {
    const inputs = document.querySelectorAll('.days-input');
    let total = 0;
    
    inputs.forEach(inp => {
        const value = parseFloat(inp.value) || 0;
        total += value;
    });
    
    const totalDisplay = document.getElementById('totalDays');
    const statusDisplay = document.getElementById('totalStatus');
    totalDisplay.textContent = total.toFixed(1);
    
    totalDisplay.style.transition = 'color 0.3s ease';
    
    if (total < 5.0) {
        totalDisplay.style.color = '#f97316';
        statusDisplay.innerHTML = `
            <div class="w-2.5 h-2.5 rounded-full bg-orange-400"></div>
            <span>Incomplete</span>
        `;
        statusDisplay.className = 'flex items-center gap-1.5 text-xs font-medium text-orange-600 ml-2';
    } else if (total === 5.0) {
        totalDisplay.style.color = '#10b981';
        statusDisplay.innerHTML = `
            <div class="w-2.5 h-2.5 rounded-full bg-green-500"></div>
            <span>Complete</span>
        `;
        statusDisplay.className = 'flex items-center gap-1.5 text-xs font-medium text-green-600 ml-2';
    } else {
        totalDisplay.style.color = '#ef4444';
        statusDisplay.innerHTML = `
            <div class="w-2.5 h-2.5 rounded-full bg-red-500"></div>
            <span>Over</span>
        `;
        statusDisplay.className = 'flex items-center gap-1.5 text-xs font-medium text-red-600 ml-2';
    }
}

async function copyLastWeek() {
    try {
        showToast('Loading last week\'s data...', 'info');
        
        const response = await fetch('api/get_history');
        if (!response.ok) throw new Error('Failed to fetch history');
        
        const data = await response.json();
        
        if (data.length === 0) {
            showToast('No previous entries found', 'error');
            return;
        }
        
        document.getElementById('rowsContainer').innerHTML = '';
        rowCounter = 0;
        
        data.forEach(entry => {
            addNewRow(entry.project, entry.days, entry.notes);
        });
        
        showToast(`Copied ${data.length} entries from last week`, 'success');
        
    } catch (error) {
        console.error('Error copying last week:', error);
        showToast('Failed to copy data', 'error');
    }
}

async function submitForm() {
    if (!currentDate) {
        showToast('Please select a week', 'error');
        return;
    }
    
    const weekDate = currentDate;
    const entryType = currentEntryType;
    
    const rows = [];
    const rowElements = document.querySelectorAll('#rowsContainer > div');
    
    rowElements.forEach(row => {
        const project = row.querySelector('.project-input').value.trim();
        const days = parseFloat(row.querySelector('.days-input').value) || 0;
        const notes = row.querySelector('.notes-input').value.trim();
        
        if (project && days > 0) {
            rows.push({ project, days, notes });
        }
    });
    
    if (rows.length === 0) {
        showToast('Add at least one entry', 'error');
        return;
    }
    
    const total = rows.reduce((sum, r) => sum + r.days, 0);
    if (total !== 5.0) {
        if (!confirm(`Total is ${total.toFixed(1)} days (target: 5.0). Submit anyway?`)) {
            return;
        }
    }
    
    try {
        showToast('Submitting timesheet...', 'info');
        
        const response = await fetch('submit', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ date: weekDate, type: entryType, rows })
        });
        
        const result = await response.json();
        
        if (response.ok) {
            showToast('Timesheet submitted successfully!', 'success');
            // Refresh activity map/outstanding items + donut chart (donut is based on actuals breakdown)
            await Promise.all([
                loadActivityMap(),
                loadOutstandingItems(),
                entryType === 'actual' ? loadMyProjectBreakdown() : Promise.resolve()
            ]);
        } else {
            showToast(result.error || 'Submit failed', 'error');
        }
    } catch (error) {
        console.error('Error:', error);
        showToast('Submit failed', 'error');
    }
}

// Team Activity Map Functions
function toggleTeamActivity() {
    const container = document.getElementById('teamActivityContainer');
    const chevron = document.getElementById('teamActivityChevron');
    const toggleText = document.getElementById('teamActivityToggleText');
    if (container && chevron) {
        const isCollapsed = container.classList.contains('collapsed');
        container.classList.toggle('collapsed');
        chevron.classList.toggle('collapsed');
        if (toggleText) {
            toggleText.textContent = isCollapsed ? 'Hide' : 'Show';
        }
    }
}

async function loadTeamActivityMaps() {
    if (!directReports || directReports.length === 0) {
        const loader = document.getElementById('teamActivityLoader');
        if (loader) loader.style.display = 'none';
        return;
    }
    
    // Load ALL team member maps 
    const fetchPromises = directReports.map(report => 
        fetch(`api/team_activity_map?member_email=${encodeURIComponent(report.email)}`)
            .then(res => res.ok ? res.json() : null)
            .then(data => ({ email: report.email, data }))
            .catch(err => {
                console.error(`Error loading activity map for ${report.name}:`, err);
                return { email: report.email, data: null };
            })
    );
    
    const results = await Promise.all(fetchPromises);
    
    // Render all maps
    results.forEach(({ email, data }) => {
        if (data) {
            renderTeamMemberActivityMap(email, data);
        }
    });
    
    // Load project breakdowns for each team member
    directReports.forEach(report => {
        const memberContainer = document.querySelector(`.team-member-activity[data-email="${report.email}"]`);
        if (memberContainer) {
            loadTeamMemberProjectBreakdown(report.email, memberContainer);
        }
    });
    
    // Hide loader, show maps
    const loader = document.getElementById('teamActivityLoader');
    const mapsContainer = document.getElementById('teamActivityMaps');
    if (loader) loader.style.display = 'none';
    if (mapsContainer) mapsContainer.style.display = 'block';
}

function renderActivityMap() {
    if (!activityMapData) return;
    
    const forecastRow = document.getElementById('forecastRow');
    const actualRow = document.getElementById('actualRow');
    const monthLabelsRow = document.getElementById('monthLabelsRow');
    
    const monthNames = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    const cellSize = 15;
    const gapSize = 2;
    
    // Calculate month spans
    let monthSpans = [];
    let currentMonth = -1;
    let currentMonthStart = 0;
    
    activityMapData.forecasts.forEach((item, index) => {
        const date = new Date(item.date + 'T00:00:00');
        const month = date.getMonth();
        
        if (month !== currentMonth) {
            if (currentMonth !== -1) {
                monthSpans.push({
                    month: currentMonth,
                    startIndex: currentMonthStart,
                    endIndex: index - 1
                });
            }
            currentMonth = month;
            currentMonthStart = index;
        }
    });
    
    if (currentMonth !== -1) {
        monthSpans.push({
            month: currentMonth,
            startIndex: currentMonthStart,
            endIndex: activityMapData.forecasts.length - 1
        });
    }
    
    // Build HTML strings
    const monthLabelsHTML = monthSpans.map(span => {
        const count = span.endIndex - span.startIndex + 1;
        const width = (count * cellSize) + ((count - 1) * gapSize);
        return `<span class="month-label" style="width:${width}px">${monthNames[span.month]}</span>`;
    }).join('');
    
    // --- UPDATED LOGIC STARTS HERE ---
    // Helper to check if this cell should be highlighted immediately on render
    const getExtraClass = (date, type) => {
        return (currentDate === date && currentEntryType === type) ? ' highlighted' : '';
    };

    const forecastHTML = activityMapData.forecasts.map(item => 
        `<div class="activity-cell cell-${item.status}${getExtraClass(item.date, 'forecast')}" 
              onclick="handleCellClick('${item.date}','forecast',${item.has_entry},'${item.status}')">
              <span class="tooltip">${formatDateLabel(item.date)} - ${getStatusLabel(item.status,'forecast')}</span>
         </div>`
    ).join('');
    
    const actualHTML = activityMapData.actuals.map(item => 
        `<div class="activity-cell cell-${item.status}${getExtraClass(item.date, 'actual')}" 
              onclick="handleCellClick('${item.date}','actual',${item.has_entry},'${item.status}')">
              <span class="tooltip">${formatDateLabel(item.date)} - ${getStatusLabel(item.status,'actual')}</span>
         </div>`
    ).join('');
    // --- UPDATED LOGIC ENDS HERE ---

    monthLabelsRow.innerHTML = monthLabelsHTML;
    forecastRow.innerHTML = forecastHTML;
    actualRow.innerHTML = actualHTML;
    
    // ... (rest of your existing donut calculation logic) ...
    
    let greenCount = 0, redCount = 0;
    activityMapData.actuals.forEach(a => {
        if (a.status === 'green') greenCount++;
        else if (a.status === 'red') redCount++;
    });
    const totalApplicable = greenCount + redCount;
    const fillPercent = totalApplicable > 0 ? Math.round((greenCount / totalApplicable) * 100) : 100;
    
    const donutCenterValue = document.getElementById('donutCenterValue');
    if (donutCenterValue) {
        donutCenterValue.textContent = `${fillPercent}%`;
        donutCenterValue.style.color = fillPercent >= 90 ? '#10b981' : fillPercent >= 70 ? '#f97316' : '#ef4444';
    }
    
    const loader = document.getElementById('myActivityLoader');
    const content = document.getElementById('myActivityContent');
    if (loader) loader.style.display = 'none';
    if (content) content.style.display = 'flex';
    
    // Explicitly scroll to the highlighted cell if it exists after render
    if (currentDate && currentEntryType) {
        highlightActivityCell(currentDate, currentEntryType);
    }
}
// Nudge Functions
async function sendNudge(toEmail, toName) {
    try {
        const response = await fetch('api/send_nudge', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ to_email: toEmail })
        });
        
        const result = await response.json();
        
        if (response.ok) {
            showToast(`Nudge sent to ${toName}! 👉`, 'success');
        } else {
            showToast(result.error || 'Failed to send nudge', 'error');
        }
    } catch (error) {
        console.error('Error sending nudge:', error);
        showToast('Failed to send nudge', 'error');
    }
}

async function checkForNudges() {
    try {
        const response = await fetch('api/get_nudges');
        if (!response.ok) return;
        
        const nudges = await response.json();
        if (nudges.length > 0) {
            pendingNudges = nudges;
            currentNudgeIndex = 0;
            await showAndDismissNudge(nudges[0]);
        }
    } catch (error) {
        console.error('Error checking nudges:', error);
    }
}

async function showAndDismissNudge(nudge) {
    // Immediately dismiss the nudge in the database so it won't show again
    try {
        await fetch('api/dismiss_nudge', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ nudge_id: nudge.id })
        });
    } catch (error) {
        console.error('Error dismissing nudge:', error);
    }
    
    // Then show the modal
    const modal = document.getElementById('nudgeModal');
    const art = document.getElementById('nudgeArt');
    const from = document.getElementById('nudgeFrom');
    const message = document.getElementById('nudgeMessage');
    
    art.textContent = nudgeArts[Math.floor(Math.random() * nudgeArts.length)];
    from.textContent = `From: ${nudge.from_name} • ${nudge.created}`;
    message.textContent = nudge.message;
    
    modal.classList.add('show');
}

function closeNudgeModal() {
    const modal = document.getElementById('nudgeModal');
    modal.classList.remove('show');
}

async function dismissCurrentNudge() {
    currentNudgeIndex++;
    
    if (currentNudgeIndex < pendingNudges.length) {
        await showAndDismissNudge(pendingNudges[currentNudgeIndex]);
    } else {
        closeNudgeModal();
        pendingNudges = [];
        currentNudgeIndex = 0;
    }
}

// Init
document.addEventListener('DOMContentLoaded', async () => {
    console.log('=== WOOP App Initializing ===');
    console.log('Page URL:', window.location.href);
    
    // Add initial row
    addNewRow();
    
    // Check health endpoint 
    try {
        const healthResponse = await fetch('api/health');
        const healthData = await healthResponse.json();
        console.log('Health check:', healthData);
    } catch (e) {
        console.warn('Health check failed:', e);
    }
    
    // Load all data in parallel
    try {
        await Promise.all([loadActivityMap(), loadOutstandingItems(), loadTeamActivityMaps(), loadMyProjectBreakdown()]);
        console.log('=== All data loaded successfully ===');
    } catch (error) {
        console.error('Error during initialization:', error);
    }
    
    // Check for any nudges 
    setTimeout(checkForNudges, 1000);
});

// Keyboard shortcuts
document.addEventListener('keydown', (e) => {
    if (e.ctrlKey && e.key === 'n') {
        e.preventDefault();
        addNewRow();
    }
    if (e.ctrlKey && e.key === 'l') {
        e.preventDefault();
        copyLastWeek();
    }
    if (e.ctrlKey && e.key === 'Enter') {
        e.preventDefault();
        submitForm();
    }
});

document.addEventListener('click', (e) => {
    if (!e.target.closest('.project-wrapper')) {
        closeAllDropdowns();
    }
});

// Week dropdown close on click outside
document.addEventListener('click', function(e) {
    const wrapper = document.getElementById('weekSelectWrapper');
    if (wrapper && !wrapper.contains(e.target)) {
        closeWeekDropdown();
    }
});

// Use passive event listeners for scroll performance
document.addEventListener('scroll', () => {}, { passive: true });
document.addEventListener('wheel', () => {}, { passive: true });
document.addEventListener('touchstart', () => {}, { passive: true });

