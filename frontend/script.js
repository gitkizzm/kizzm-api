document.getElementById('sendBtn').addEventListener('click', async () => {
    const deckersteller = document.getElementById('deckersteller').value;
    const commander = document.getElementById('commander').value;
    const deckUrl = document.getElementById('deckUrl').value;

    if (!deckersteller || !commander) {
        alert('Bitte füllen Sie alle erforderlichen Felder aus!');
        return;
    }

    const data = { deckersteller, commander, deckUrl: deckUrl || null };

    try {
        const response = await fetch('https://your-back4app-url/save', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });

        if (response.ok) {
            window.location.href = 'success.html';
        } else {
            const error = await response.json();
            alert(`Fehler: ${error.detail}`);
        }
    } catch (err) {
        console.error('Fehler:', err);
        alert('Serverfehler. Bitte versuchen Sie es später erneut.');
    }
});
