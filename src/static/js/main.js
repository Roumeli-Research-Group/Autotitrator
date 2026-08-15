
document.addEventListener('DOMContentLoaded', () => {
    // Start global status polling. Probe values are cached server-side
    // (PROBE_CACHE_TTL), so this does not hammer the I2C bus.
    setInterval(updateGlobalStatus, 4000);
    updateGlobalStatus();

    // Setup mobile menu config if needed (not implemented in CSS yet but good practice)
});

async function updateGlobalStatus() {
    try {
        const response = await fetch('/api/status');
        const data = await response.json();

        const pumpBadge = document.getElementById('status-pump');
        const measureBadge = document.getElementById('status-measure');

        if (pumpBadge) {
            updateBadge(pumpBadge, data.pump_status, 'Pump ON', 'Pump OFF');
        }

        if (measureBadge) {
            updateBadge(measureBadge, data.measurement_status, 'Measuring', 'Idle');
        }

        setBadgeValue('status-ph', 'pH', data.ph, 2);
        setBadgeValue('status-ec', 'EC', data.ec, 0, ' µS/cm');
        setBadgeValue('status-temp', 'Temp', data.temp, 1, ' °C');
    } catch (error) {
        console.error('Failed to fetch status:', error);
    }
}

function setBadgeValue(id, label, value, decimals, suffix = '') {
    const el = document.getElementById(id);
    if (!el) return;
    const text = (value === null || value === undefined)
        ? `${label}: --`
        : `${label}: ${Number(value).toFixed(decimals)}${suffix}`;
    el.querySelector('span').textContent = text;
}

function updateBadge(element, isActive, activeText, inactiveText) {
    if (isActive) {
        element.classList.remove('off');
        element.classList.add('on');
        element.querySelector('span').textContent = activeText;
    } else {
        element.classList.remove('on');
        element.classList.add('off');
        element.querySelector('span').textContent = inactiveText;
    }
}

// Utility for pump control
async function controlPump(action) {
    try {
        const endpoint = action === 'start' ? '/start_pump' : '/stop_pump';
        const response = await fetch(endpoint, { method: 'POST' });
        const data = await response.json();

        if (data.error) {
            showNotification(data.error, 'error');
        } else {
            showNotification(data.message, 'success');
            updateGlobalStatus(); // Immediate update
        }
    } catch (error) {
        showNotification(error.message, 'error');
    }
}

function showNotification(message, type = 'info') {
    // Simple alert for now, could be a toast in future
    // Or we can create a toast container in layout
    // For now, let's just log or alert if critical
    if (type === 'error') {
        alert(message);
    } else {
        console.log(message);
    }
}
