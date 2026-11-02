let count = 0;
function incrementCounter() {
    count += 1;
    document.getElementById('counter').innerText = 'Count ' + count;
}
document.addEventListener("DOMContentLoaded", function() {
    const imgElement = document.getElementById("logo");
    if (imgElement) {
        imgElement.src = 'https://www.jdoodle.com/assets/JD_logo_colour-94056f51.webp';
    }
});
