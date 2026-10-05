var API_BASE = "https://vicidial97.directo.com/cortizovpn-api/";

function apiUrl(path) {
  return API_BASE + path.replace(/^\//, "");
}

function setHealth(ok) {
  var badge = document.getElementById("healthBadge");
  badge.textContent = ok ? "Backend operativo" : "Backend sin respuesta";
  badge.className = ok ? "badge badge-ok" : "badge badge-error";
}

function fillGroups(items) {
  var select = document.getElementById("userGroup");
  var body = document.getElementById("groupsBody");
  var status = document.getElementById("groupsStatus");

  select.innerHTML = "";
  body.innerHTML = "";

  if (!items.length) {
    select.innerHTML = "<option>Sin grupos</option>";
    body.innerHTML = '<tr><td colspan="3">No se encontraron grupos.</td></tr>';
    status.textContent = "0 grupos encontrados";
    return;
  }

  items.forEach(function (group) {
    var option = document.createElement("option");
    option.value = group.user_group;
    option.textContent = group.user_group + " — " + (group.group_name || "");
    select.appendChild(option);

    var row = document.createElement("tr");
    row.innerHTML =
      "<td><strong>" + escapeHtml(group.user_group || "") + "</strong></td>" +
      "<td>" + escapeHtml(group.group_name || "") + "</td>" +
      "<td>" + escapeHtml(group.allowed_campaigns || "") + "</td>";
    body.appendChild(row);
  });

  select.disabled = false;
  status.textContent = items.length + " grupos encontrados";
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function loadHealth() {
  fetch(apiUrl("health"), { cache: "no-store" })
    .then(function (response) {
      if (!response.ok) throw new Error("HTTP " + response.status);
      return response.json();
    })
    .then(function () { setHealth(true); })
    .catch(function () { setHealth(false); });
}

function loadGroups() {
  var status = document.getElementById("groupsStatus");
  status.textContent = "Consultando MASTER…";

  fetch(apiUrl("user-groups"), { cache: "no-store" })
    .then(function (response) {
      return response.json().then(function (data) {
        if (!response.ok) throw new Error(data.error || ("HTTP " + response.status));
        return data;
      });
    })
    .then(function (data) { fillGroups(data.items || []); })
    .catch(function (error) {
      document.getElementById("userGroup").disabled = true;
      document.getElementById("groupsBody").innerHTML =
        '<tr><td colspan="3">Error al consultar grupos.</td></tr>';
      status.textContent = "Error: " + error.message;
    });
}

var previewTimer = null;

function schedulePreview() {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(loadUsernamePreview, 350);
}

function loadUsernamePreview() {
  var first = document.getElementById("firstName").value.trim();
  var second = document.getElementById("secondName").value.trim();
  var paternal = document.getElementById("paternalSurname").value.trim();
  var maternal = document.getElementById("maternalSurname").value.trim();
  var username = document.getElementById("username");
  var fullName = document.getElementById("fullName");
  var status = document.getElementById("usernameStatus");

  if (!first || !paternal) {
    username.value = "";
    fullName.value = "";
    status.textContent = "Captura primer nombre y apellido paterno.";
    return;
  }

  status.textContent = "Validando contra vicidial_users…";

  var query = new URLSearchParams({
    first_name: first,
    second_name: second,
    paternal_surname: paternal,
    maternal_surname: maternal
  });

  fetch(apiUrl("users/preview?" + query.toString()), { cache: "no-store" })
    .then(function (response) {
      return response.json().then(function (data) {
        if (!response.ok) throw new Error(data.error || ("HTTP " + response.status));
        return data;
      });
    })
    .then(function (data) {
      username.value = data.username || "";
      fullName.value = data.full_name || "";
      status.textContent = data.collision
        ? "Se detectó colisión; usuario alternativo disponible: " + data.username
        : "Usuario disponible: " + data.username;
    })
    .catch(function (error) {
      username.value = "";
      fullName.value = "";
      status.textContent = "Error al validar usuario: " + error.message;
    });
}

["firstName", "secondName", "paternalSurname", "maternalSurname"].forEach(function (id) {
  document.getElementById(id).addEventListener("input", schedulePreview);
});

document.getElementById("reloadGroups").addEventListener("click", loadGroups);

loadHealth();
loadGroups();
