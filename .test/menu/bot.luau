_G.Files.Bots = resolvePath("bots.json")

local File = Files.Bots
local bots = {}
local list = {}

local function save()
	fs.writeDir(resolvePath())
	fs.writeFile(File, serde.encode("json", list, true))
end

local function taken(name: string): boolean
	for _, cfg in list do
		if cfg.Name == name then
			return true
		end
	end
	return false
end

local function folderOf(cfg)
	local folder = { Name = cfg.Name }

	local nameItem = { Type = "String", Name = "Name", Value = cfg.Name }
	nameItem.Func = function(value: string)
		value = value:match("^%s*(.-)%s*$")
		if value == "" or (value ~= cfg.Name and taken(value)) then
			m:log("Invalid or used name:", value)
			nameItem.Value = cfg.Name
			return
		end
		cfg.Name = value
		folder.Name = value
	end

	folder[1] = nameItem
	folder[2] = {
		Type = "Dropdown",
		Name = "Edition",
		Options = { "Bedrock", "Java" },
		Value = cfg.Edition,
		Func = function(value: string)
			cfg.Edition = value
		end,
	}
	folder[3] = {
		Type = "Boolean",
		Name = "Online-mode",
		Value = cfg["Online-mode"],
		Func = function(value: boolean)
			cfg["Online-mode"] = value
		end,
	}
	return folder
end

local function sync()
	for i = #bots, 1, -1 do
		bots[i] = nil
	end
	for i, cfg in list do
		bots[i] = folderOf(cfg)
	end
end

bots.refresh = { Type = "OnCall", Func = sync }

bots.add = {
	Type = "String",
	Name = "add",
	Value = "",
	Func = function(value: string)
		bots.add.Value = ""
		local name = value:match("^%s*(.-)%s*$")
		if name == "" or taken(name) then
			m:log("Invalid or used name:", name)
			return
		end
		local cfg = { Name = name, Edition = "Bedrock", ["Online-mode"] = false }
		table.insert(list, cfg)
		table.insert(bots, folderOf(cfg))
		m:log("Added bot:", name)
	end,
}

if fs.isFile(File) then
	list = serde.decode("json", fs.readFile(File))
	sync()
end

task.spawn(function()
	m:wait()
	save()
end)

_G.menu:tree().bots = bots