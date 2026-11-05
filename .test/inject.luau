local env = _G :: any
local T = table :: any

local function env.resolvePath(rel: string?): string
	local env = process.env
	local base: string

	if process.os == "windows" then
		base = env.APPDATA or (env.USERPROFILE .. "/AppData/Roaming")
	elseif process.os == "macos" then
		base = env.HOME .. "/Library/Application Support"
	else
		base = env.XDG_DATA_HOME or (env.HOME .. "/.local/share")
	end

	local path = base:gsub("\\", "/") .. "/" .. Luaxite
	if rel then
		path ..= "/" .. rel:gsub("\\", "/")
	end
	return path
end

mkdir(resolvePath())
env.Files = {}










function T.forEach(t: { any }, fn: (any, number, { any }) -> ())
	for i, v in ipairs(t) do
		fn(v, i, t)
	end
end

function T.map(t: { any }, fn: (any, number, { any }) -> any): { any }
	local out = {}
	for i, v in ipairs(t) do
		out[i] = fn(v, i, t)
	end
	return out
end

function T.filter(t: { any }, fn: (any, number, { any }) -> boolean): { any }
	local out = {}
	for i, v in ipairs(t) do
		if fn(v, i, t) then
			table.insert(out, v)
		end
	end
	return out
end

function T.reduce(t: { any }, fn: (any, any, number, { any }) -> any, ...: any): any
	local acc, start = ..., 1
	if select("#", ...) == 0 then
		assert(#t > 0, "reduce of an empty table with no initial value")
		acc, start = t[1], 2
	end
	for i = start, #t do
		acc = fn(acc, t[i], i, t)
	end
	return acc
end

function T.some(t: { any }, fn: (any, number, { any }) -> boolean): boolean
	for i, v in ipairs(t) do
		if fn(v, i, t) then
			return true
		end
	end
	return false
end

function T.every(t: { any }, fn: (any, number, { any }) -> boolean): boolean
	for i, v in ipairs(t) do
		if not fn(v, i, t) then
			return false
		end
	end
	return true
end

-function T.findWhere(t: { any }, fn: (any, number, { any }) -> boolean): (any, number?)
	for i, v in ipairs(t) do
		if fn(v, i, t) then
			return v, i
		end
	end
	return nil, nil
end

function T.findIndex(t: { any }, fn: (any, number, { any }) -> boolean): number?
	local _, i = T.findWhere(t, fn)
	return i
end

function T.includes(t: { any }, value: any): boolean
	return table.find(t, value) ~= nil
end

function T.slice(t: { any }, first: number?, last: number?): { any }
	local n = #t
	local a, b = first or 1, last or n
	if a < 0 then a = n + a + 1 end
	if b < 0 then b = n + b + 1 end
	a, b = math.max(a, 1), math.min(b, n)
	local out = {}
	if a <= b then
		table.move(t, a, b, 1, out)
	end
	return out
end

function T.reverse(t: { any }): { any }
	local out, n = {}, #t
	for i = 1, n do
		out[i] = t[n - i + 1]
	end
	return out
end

function T.flat(t: { any }, depth: number?): { any }
	local d = depth or 1
	local out = {}
	for _, v in ipairs(t) do
		if type(v) == "table" and d > 0 then
			for _, x in ipairs(T.flat(v, d - 1)) do
				table.insert(out, x)
			end
		else
			table.insert(out, v)
		end
	end
	return out
end

function T.flatMap(t: { any }, fn: (any, number, { any }) -> any): { any }
	return T.flat(T.map(t, fn), 1)
end

function T.keys(t: { [any]: any }): { any }
	local out = {}
	for k in t do
		table.insert(out, k)
	end
	return out
end

function T.values(t: { [any]: any }): { any }
	local out = {}
	for _, v in t do
		table.insert(out, v)
	end
	return out
end

function T.entries(t: { [any]: any }): { { any } }
	local out = {}
	for k, v in t do
		table.insert(out, { k, v })
	end
	return out
end

function env.isfile(p: string): boolean
	return fs.isFile(p)
end

function env.isfolder(p: string): boolean
	return fs.isDir(p)
end

function env.readfile(p: string): string
	return fs.readFile(p)
end

function env.writefile(p: string, data: string)
	local path = p
	local dir = path:match("^(.*)/[^/]+$")
	if dir and dir ~= "" and not fs.isDir(dir) then
		fs.writeDir(dir)
	end
	fs.writeFile(path, data)
end

function env.appendfile(p: string, data: string)
	env.writefile(p, (env.isfile(p) and env.readfile(p) or "") .. data)
end

function env.makefolder(p: string)
	if not env.isfolder(p) then
		fs.writeDir(p)
	end
end

function env.delfile(p: string)
	if env.isfile(p) then
		fs.removeFile(p)
	end
end

function env.delfolder(p: string)
	if env.isfolder(p) then
		fs.removeDir(p)
	end
end

function env.listfiles(p: string): { string }
	local out = {}
	for _, name in fs.readDir(p) do
		table.insert(out, (p:gsub("/+$", "")) .. "/" .. name)
	end
	return out
end

function env.loadfile(p: string)
	local ok, res = pcall(function()
		return luau.load(env.readfile(p), { debugName = "@" .. p })
	end)
	if ok then
		return res
	end
	return nil, res
end

function env.dofile(p: string, ...)
	return assert(env.loadfile(p))(...)
end

function env.loadstring(source: string, chunkname: string?)
	local ok, res = pcall(luau.load, source, { debugName = chunkname })
	if ok then
		return res
	end
	return nil, tostring(res)
end

function env.getgenv()
	return _G
end

function env.getrenv()
	return _G
end

function env.request(options: {
	Url: string,
	Method: string?,
	Body: string?,
	Headers: { [string]: string }?,
	Cookies: { [string]: string }?,
})
	assert(type(options) == "table" and type(options.Url) == "string", "request: options.Url must be a string")

	local headers = {}
	for k, v in options.Headers or {} do
		headers[k] = tostring(v)
	end
	if options.Cookies then
		local parts = {}
		for k, v in options.Cookies do
			table.insert(parts, k .. "=" .. tostring(v))
		end
		headers["Cookie"] = table.concat(parts, "; ")
	end

	local ok, res = pcall(net.request, {
		url = options.Url,
		method = options.Method and string.upper(options.Method) or "GET",
		body = options.Body,
		headers = headers,
	})
	if not ok then
		return { Success = false, Body = "", StatusCode = 0, StatusMessage = tostring(res), Headers = {} }
	end

	return {
		Success = res.ok,
		Body = res.body,
		StatusCode = res.statusCode,
		StatusMessage = res.statusMessage,
		Headers = res.headers,
	}
end