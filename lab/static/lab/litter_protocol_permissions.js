document.addEventListener("DOMContentLoaded", function () {
        const protocolSelect = document.getElementById("id_protocol");
        const procedureFieldset = document.querySelector("[data-procedure-fieldset]");
        const disabledHelp = document.querySelector("[data-procedure-disabled-help]");
        const permissionsElement = document.getElementById("protocol-procedure-permissions");

        if (!protocolSelect || !procedureFieldset || !permissionsElement) {
            return;
        }

        const protocolPermissions = JSON.parse(permissionsElement.textContent);

        function updateProcedureFields() {
            const selectedProtocol = protocolSelect.value;
            const canAddProcedure = (
                Boolean(selectedProtocol)
                && protocolPermissions[selectedProtocol] !== false
            );

            procedureFieldset.disabled = !canAddProcedure;

            if (disabledHelp) {
                disabledHelp.hidden = canAddProcedure;
            }
        }

        protocolSelect.addEventListener("change", updateProcedureFields);
        updateProcedureFields();
    });
