// This script will run on every page load in the Desk.

// We use a router event listener to check the page every time the user navigates.
frappe.router.on('change', () => {
    handle_new_button_visibility();
});

// We also run it once on initial load.
$(document).ready(() => {
    handle_new_button_visibility();
});

function handle_new_button_visibility() {
    // This is the jQuery selector for the global '+ New' button.
    const new_button = $('button.page-actions-btn.btn-primary');

    // Check if the current user has the 'Workspace Restricted' role 
    // AND if the current page is a workspace.
    if (frappe.user_has_role('Workspace Restricted') && frappe.container.page.doctype === 'Workspace') {
        // If true, hide the button.
        new_button.hide();
    } else {
        // On all other pages, ensure the button is visible.
        new_button.show();
    }
}