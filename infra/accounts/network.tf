# Optional subnet creation. No fixed CIDRs, no new VPC, NAT, Internet gateway,
# or changes to the existing website subnet/route table.
resource "aws_subnet" "database" {
  for_each                = var.private_db_subnets
  vpc_id                  = var.vpc_id
  cidr_block              = each.value.cidr_block
  availability_zone       = each.value.availability_zone
  map_public_ip_on_launch = false
  tags = {
    Name = "${local.name}-${each.key}"
  }
}

resource "aws_route_table" "database" {
  count  = length(var.private_db_subnets) > 0 ? 1 : 0
  vpc_id = var.vpc_id
  # An explicit empty route list removes unmanaged non-local routes on refresh.
  # AWS retains the VPC-local route needed to reach EC2 in the same VPC.
  route = []
  tags = {
    Name = "${local.name}-private"
  }
}

resource "aws_route_table_association" "database" {
  for_each       = var.private_db_subnets
  subnet_id      = aws_subnet.database[each.key].id
  route_table_id = aws_route_table.database[0].id
}

locals {
  database_subnet_ids = concat(
    tolist(var.private_db_subnet_ids),
    [for subnet in aws_subnet.database : subnet.id],
  )
  database_subnet_azs = concat(
    [for subnet in data.aws_subnet.database : subnet.availability_zone],
    [for subnet in var.private_db_subnets : subnet.availability_zone],
  )
}
